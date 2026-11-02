/* SPDX-License-Identifier: GPL-2.0 */
/* Fixed 800x480 Linux KMS/evdev bring-up for the AN970 panel. */
#include <arpa/inet.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <drm_fourcc.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

#define FRAME_BYTES (800 * 480 * 4)
#define META_BYTES 80
#define FRAME_HEADER_BYTES (8 + 2 * META_BYTES)
#define FRAME_MAP_BYTES (FRAME_HEADER_BYTES + 2 * FRAME_BYTES)
#define CARD_WIDTH 176
#define CARD_HEIGHT 89
#define CARD_BYTES (CARD_WIDTH * CARD_HEIGHT * 4)
#define STRIP_WIDTH 415
#define SLIDER_LEFT 64
#define SLIDER_RIGHT 452
#define SLIDER_WIDTH (SLIDER_RIGHT - SLIDER_LEFT)
#define COUNT_CARD_X 170
#define STRIP_HEIGHT 30
#define STRIP_BYTES (STRIP_WIDTH * STRIP_HEIGHT * 4)
#define CARDS_MAP_BYTES (400 * CARD_BYTES + STRIP_BYTES + 25 * 25 * 4)

static uint32_t *pixels;
static unsigned int stride;
static volatile sig_atomic_t running = 1;
static unsigned char *count_cards;
static uint64_t native_draw_ns, native_draw_max;
static unsigned int native_draw_updates;
static int scroll_fd = -1;
static int shared_frame_fd = -1;
static const volatile uint32_t *published_generation;
static unsigned char *scroll_data;
static size_t scroll_map_bytes;
static uint64_t scroll_draw_ns, scroll_draw_max;
static unsigned int scroll_draw_updates;

struct damage_rect {
    int left, top, right, bottom, valid;
};

struct display_buffer {
    struct drm_mode_create_dumb create;
    uint32_t framebuffer;
    uint32_t *mapping;
    struct damage_rect dirty;
};

static struct {
    struct display_buffer buffers[2];
    int front, queued, pending;
    uint64_t scene_version, queued_version, shown_version;
    uint64_t flips, copied_bytes, full_copies, partial_copies;
} display;

static void damage(int left, int top, int right, int bottom)
{
    if (right == left || bottom == top)
        return;
    for (int i = 0; i < 2; ++i) {
        struct damage_rect *d = &display.buffers[i].dirty;
        if (!d->valid) {
            *d = (struct damage_rect){left, top, right, bottom, 1};
        } else {
            if (left < d->left) d->left = left;
            if (top < d->top) d->top = top;
            if (right > d->right) d->right = right;
            if (bottom > d->bottom) d->bottom = bottom;
        }
    }
    ++display.scene_version;
}

static void copy_buffer(int index)
{
    struct display_buffer *buffer = &display.buffers[index];
    struct damage_rect *d = &buffer->dirty;
    if (!d->valid)
        return;
    unsigned int target_stride = buffer->create.pitch/4;
    size_t row_bytes = (d->right-d->left)*4;
    for (int row = d->top; row < d->bottom; ++row)
        memcpy(buffer->mapping+row*target_stride+d->left,
               pixels+row*stride+d->left, row_bytes);
    display.copied_bytes += row_bytes*(d->bottom-d->top);
    if (d->left == 0 && d->top == 0 && d->right == 800 && d->bottom == 480)
        ++display.full_copies;
    else
        ++display.partial_copies;
    d->valid = 0;
}

static void flip_complete(int fd, unsigned int sequence, unsigned int seconds,
                          unsigned int microseconds, void *data)
{
    (void)fd; (void)sequence; (void)seconds; (void)microseconds; (void)data;
    display.front = display.queued;
    display.shown_version = display.queued_version;
    display.pending = 0;
    if (++display.flips == 1) {
        puts("LCD_KMS_FLIP_READY buffers=2");
        fflush(stdout);
    }
}

static int present_display(int drm, uint32_t crtc)
{
    if (display.pending || display.shown_version == display.scene_version)
        return 0;
    int back = display.front ^ 1;
    copy_buffer(back);
    if (drmModePageFlip(drm, crtc, display.buffers[back].framebuffer,
                        DRM_MODE_PAGE_FLIP_EVENT, NULL)) {
        perror("submit LCD page flip");
        return -1;
    }
    display.queued = back;
    display.queued_version = display.scene_version;
    display.pending = 1;
    return 0;
}

static pthread_mutex_t drag_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t drag_ready = PTHREAD_COND_INITIALIZER;
static int drag_pending, drag_busy, drag_stop;
static int drag_x, drag_y, drag_x0, drag_y0;

static void screen_touch(int port, int x, int y, int x0, int y0, int drag)
{
    int connection = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in address = {.sin_family = AF_INET,
                                  .sin_port = htons(port)};
    inet_pton(AF_INET, "127.0.0.1", &address.sin_addr);
    if (connect(connection, (struct sockaddr *)&address, sizeof(address))) {
        perror("connect screen controller");
        close(connection);
        return;
    }
    char body[128], request[512], response[512];
    int length = snprintf(body, sizeof(body), "{\"x\":%d,\"y\":%d,\"x0\":%d,\"y0\":%d,\"drag\":%s}",
                          x, y, x0, y0, drag ? "true" : "false");
    int request_length = snprintf(request, sizeof(request),
        "POST /touch HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
        "Content-Length: %d\r\nConnection: close\r\n\r\n%s", length, body);
    send(connection, request, request_length, MSG_NOSIGNAL);
    while (recv(connection, response, sizeof(response), 0) > 0) {}
    close(connection);
    if (!drag) {
        printf("SCREEN_GESTURE %d %d -> %d %d\n", x0, y0, x, y);
        fflush(stdout);
    }
}

static void *drag_worker(void *port_pointer)
{
    int port = *(int *)port_pointer;
    pthread_mutex_lock(&drag_lock);
    while (!drag_stop) {
        while (!drag_pending && !drag_stop)
            pthread_cond_wait(&drag_ready, &drag_lock);
        if (drag_stop)
            break;
        int x = drag_x, y = drag_y, x0 = drag_x0, y0 = drag_y0;
        drag_pending = 0;
        drag_busy = 1;
        pthread_mutex_unlock(&drag_lock);
        screen_touch(port, x, y, x0, y0, 1);
        pthread_mutex_lock(&drag_lock);
        drag_busy = 0;
        pthread_cond_broadcast(&drag_ready);
    }
    pthread_mutex_unlock(&drag_lock);
    return NULL;
}

static void stop_display(int signal_number)
{
    (void)signal_number;
    running = 0;
}

static const unsigned char digits[10][7] = {
    {14, 17, 19, 21, 25, 17, 14}, {4, 12, 4, 4, 4, 4, 14},
    {14, 17, 1, 2, 4, 8, 31}, {30, 1, 1, 14, 1, 1, 30},
    {2, 6, 10, 18, 31, 2, 2}, {31, 16, 16, 30, 1, 1, 30},
    {6, 8, 16, 30, 17, 17, 14}, {31, 1, 2, 4, 8, 8, 8},
    {14, 17, 17, 14, 17, 17, 14}, {14, 17, 17, 15, 1, 2, 12}
};

static void fill(int x, int y, int w, int h, uint32_t color)
{
    for (int row = y; row < y + h; ++row)
        for (int col = x; col < x + w; ++col)
            pixels[row * stride + col] = color;
}

static int draw_count(int touch_x)
{
    struct timespec begin, end;
    clock_gettime(CLOCK_MONOTONIC, &begin);
    int x = touch_x < SLIDER_LEFT ? SLIDER_LEFT : (touch_x > SLIDER_RIGHT ? SLIDER_RIGHT : touch_x);
    int count = 1 + ((x-SLIDER_LEFT)*399+SLIDER_WIDTH/2)/SLIDER_WIDTH;
    const uint32_t *card = (const uint32_t *)(count_cards+(count-1)*CARD_BYTES);
    for (int row = 0; row < CARD_HEIGHT; ++row)
        memcpy(pixels+(181+row)*stride+COUNT_CARD_X, card+row*CARD_WIDTH, CARD_WIDTH*4);
    const uint32_t *strip = (const uint32_t *)(count_cards+400*CARD_BYTES);
    for (int row = 0; row < STRIP_HEIGHT; ++row)
        memcpy(pixels+(287+row)*stride+SLIDER_LEFT-13, strip+row*STRIP_WIDTH, STRIP_WIDTH*4);
    fill(SLIDER_LEFT, 298, x-SLIDER_LEFT+1, 8, 0xff287d68);
    const uint32_t *thumb = (const uint32_t *)(count_cards+400*CARD_BYTES+STRIP_BYTES);
    for (int row = 0; row < 25; ++row)
        for (int col = 0; col < 25; ++col) {
            uint32_t pixel = thumb[row*25+col];
            if (pixel >> 24)
                pixels[(289+row)*stride+x-12+col] = pixel;
        }
    clock_gettime(CLOCK_MONOTONIC, &end);
    uint64_t elapsed = (end.tv_sec-begin.tv_sec)*1000000000LL + end.tv_nsec-begin.tv_nsec;
    native_draw_ns += elapsed;
    if (elapsed > native_draw_max)
        native_draw_max = elapsed;
    ++native_draw_updates;
    damage(SLIDER_LEFT-13, 181, SLIDER_LEFT-13+STRIP_WIDTH, 317);
    return count;
}

static int scroll_hit(const uint32_t *h, int x, int y)
{
    if (h[2] == 1)
        return 508 <= x && x <= 776 && 213 <= y && y <= 386;
    if (h[2] == 2)
        return 38 <= x && x <= 336 && 209 <= y && y <= 393;
    if (h[2] == 3)
        return 48 <= x && x <= 752 && 187 <= y && y <= 406;
    return 0;
}

static int draw_scroll(const uint32_t *h, int offset)
{
    if (flock(shared_frame_fd, LOCK_SH | LOCK_NB))
        return 0;
    if (*published_generation != h[0]) {
        flock(shared_frame_fd, LOCK_UN);
        return 0;
    }
    struct timespec begin, end;
    clock_gettime(CLOCK_MONOTONIC, &begin);
    size_t needed = (size_t)h[11]*2;
    if (needed > scroll_map_bytes) {
        munmap(scroll_data, scroll_map_bytes);
        scroll_data = mmap(NULL, needed, PROT_READ, MAP_SHARED, scroll_fd, 0);
        scroll_map_bytes = needed;
    }
    const uint32_t *source = (const uint32_t *)(scroll_data+(h[12]&1)*(size_t)h[11]);
    for (unsigned int row = 0; row < h[6]; ++row)
        memcpy(pixels+(h[4]+row)*stride+h[3], source+(offset+row)*h[5], h[5]*4);
    if (h[10]) {
        int thumb = h[15]*h[6]/h[7];
        if (thumb < 12) thumb = 12;
        int top = h[14]+offset*(h[15]-thumb)/h[10];
        fill(h[13], h[14], h[16], h[15], 0xffe7ede7);
        fill(h[13], top, h[16], thumb, 0xff90b6a2);
    }
    flock(shared_frame_fd, LOCK_UN);
    clock_gettime(CLOCK_MONOTONIC, &end);
    uint64_t elapsed = (end.tv_sec-begin.tv_sec)*1000000000LL+end.tv_nsec-begin.tv_nsec;
    scroll_draw_ns += elapsed;
    if (elapsed > scroll_draw_max) scroll_draw_max = elapsed;
    ++scroll_draw_updates;
    int right = h[3]+h[5], bottom = h[4]+h[6];
    if ((int)(h[13]+h[16]) > right) right = h[13]+h[16];
    if ((int)(h[14]+h[15]) > bottom) bottom = h[14]+h[15];
    damage(h[3], h[4], right, bottom);
    return 1;
}

static void number(unsigned int value, int x, int y, int scale)
{
    char text[24];
    snprintf(text, sizeof(text), "%u", value);
    for (unsigned int n = 0; n < strlen(text); ++n) {
        const unsigned char *glyph = digits[text[n] - '0'];
        for (int row = 0; row < 7; ++row)
            for (int col = 0; col < 5; ++col)
                if (glyph[row] & (1 << (4 - col)))
                    fill(x + (n * 6 + col) * scale, y + row * scale,
                         scale, scale, 0xff173f3a);
    }
}

static void button(int index, unsigned int count)
{
    int x = 20 + (index % 3) * 260;
    int y = 20 + (index / 3) * 240;
    fill(x, y, 240, 200, count & 1 ? 0xffb8e4d1 : 0xffdbeafa);
    number(index + 1, x + 14, y + 12, 3);
    number(count, x + 70, y + 72, 10);
    damage(x, y, x+240, y+200);
}

int main(int argc, char **argv)
{
    if (argc != 3 && argc != 6) {
        fprintf(stderr, "usage: %s /dev/dri/cardN /dev/input/eventN [screen.argb controller_port count_cards.argb]\n", argv[0]);
        return 2;
    }
    int business = argc == 6;
    int frame_fd = -1, cards_fd = -1, controller_port = 0;
    unsigned char *screen_frame = NULL;
    uint32_t generation = 0;
    if (business) {
        controller_port = atoi(argv[4]);
        frame_fd = open(argv[3], O_RDONLY);
        if (frame_fd < 0) {
            perror("open screen frame");
            return 1;
        }
        screen_frame = mmap(NULL, FRAME_MAP_BYTES, PROT_READ, MAP_SHARED, frame_fd, 0);
        if (screen_frame == MAP_FAILED) {
            perror("map screen frame");
            return 1;
        }
        shared_frame_fd = frame_fd;
        published_generation = (const uint32_t *)screen_frame;
        cards_fd = open(argv[5], O_RDONLY);
        if (cards_fd < 0) {
            perror("open count cards");
            return 1;
        }
        count_cards = mmap(NULL, CARDS_MAP_BYTES, PROT_READ, MAP_PRIVATE | MAP_POPULATE, cards_fd, 0);
        if (count_cards == MAP_FAILED) {
            perror("map count cards");
            return 1;
        }
        char scroll_path[1024];
        const char *slash = strrchr(argv[3], '/');
        snprintf(scroll_path, sizeof(scroll_path), "%.*sscroll.argb",
                 slash ? (int)(slash-argv[3]+1) : 0, argv[3]);
        scroll_fd = open(scroll_path, O_RDONLY);
        if (scroll_fd < 0) { perror("open scroll atlas"); return 1; }
        struct stat scroll_stat;
        fstat(scroll_fd, &scroll_stat);
        scroll_map_bytes = scroll_stat.st_size;
        scroll_data = mmap(NULL, scroll_map_bytes, PROT_READ, MAP_SHARED, scroll_fd, 0);
        if (scroll_data == MAP_FAILED) { perror("map scroll atlas"); return 1; }
    }
    struct sigaction stop_action = {.sa_handler = stop_display};
    sigemptyset(&stop_action.sa_mask);
    sigaction(SIGTERM, &stop_action, NULL);
    sigaction(SIGINT, &stop_action, NULL);
    int drm = open(argv[1], O_RDWR);
    int input = open(argv[2], O_RDONLY);
    if (drm < 0 || input < 0) {
        perror("open display/input");
        return 1;
    }
    drmModeRes *resources = drmModeGetResources(drm);
    if (!resources || !resources->count_connectors || !resources->count_crtcs) {
        fprintf(stderr, "The selected device has no LCD KMS output\n");
        return 1;
    }
    drmModeConnector *connector = drmModeGetConnector(drm, resources->connectors[0]);
    if (!connector || !connector->count_modes) {
        fprintf(stderr, "The LCD connector has no mode\n");
        return 1;
    }
    drmModeModeInfo mode = connector->modes[0];
    for (int i = 0; i < 2; ++i) {
        struct display_buffer *buffer = &display.buffers[i];
        buffer->create = (struct drm_mode_create_dumb){.width = 800, .height = 480, .bpp = 32};
        if (ioctl(drm, DRM_IOCTL_MODE_CREATE_DUMB, &buffer->create)) {
            perror("create display buffer");
            return 1;
        }
        uint32_t handles[4] = {buffer->create.handle};
        uint32_t pitches[4] = {buffer->create.pitch}, offsets[4] = {0};
        if (drmModeAddFB2(drm, 800, 480, DRM_FORMAT_ARGB8888,
                          handles, pitches, offsets, &buffer->framebuffer, 0)) {
            perror("register framebuffer");
            return 1;
        }
        struct drm_mode_map_dumb map = {.handle = buffer->create.handle};
        if (ioctl(drm, DRM_IOCTL_MODE_MAP_DUMB, &map)) {
            perror("map framebuffer offset");
            return 1;
        }
        buffer->mapping = mmap(NULL, buffer->create.size, PROT_READ | PROT_WRITE,
                               MAP_SHARED, drm, map.offset);
        if (buffer->mapping == MAP_FAILED) {
            perror("map framebuffer");
            return 1;
        }
    }
    /* All drawing goes to this CPU scene, never the currently scanned buffer. */
    stride = 800;
    pixels = calloc(480*stride, 4);
    if (!pixels) {
        perror("allocate display scene");
        return 1;
    }
    fill(0, 0, 800, 480, 0xfff3f8fc);
    unsigned int counts[6] = {0};
    if (!business)
        for (int i = 0; i < 6; ++i)
            button(i, 0);
    damage(0, 0, 800, 480);
    copy_buffer(0);
    copy_buffer(1);
    display.shown_version = display.scene_version;
    if (drmModeSetCrtc(drm, resources->crtcs[0], display.buffers[0].framebuffer, 0, 0,
                       &connector->connector_id, 1, &mode)) {
        perror("start LCD scanout");
        return 1;
    }
    printf("LCD_KMS_READY %ux%u; %s\n", mode.hdisplay, mode.vdisplay,
           business ? "screen TIFF session" : "six buttons: release to increment");
    fflush(stdout);
    struct input_absinfo abs_x, abs_y;
    ioctl(input, EVIOCGABS(ABS_X), &abs_x);
    ioctl(input, EVIOCGABS(ABS_Y), &abs_y);
    int x = abs_x.value, y = abs_y.value, down = 0, previous_down = 0;
    int x0 = x, y0 = y;
    int native_count = 0;
    uint32_t scroll_header[20] = {0};
    int scroll_active = 0, scroll_kind = 0, scroll_origin = 0, native_offset = -1;
    struct timespec drag_last = {0};
    pthread_t drag_thread;
    if (business)
        pthread_create(&drag_thread, NULL, drag_worker, &controller_port);
    struct pollfd events[2] = {{.fd = input, .events = POLLIN},
                               {.fd = drm, .events = POLLIN}};
    drmEventContext drm_events = {.version = DRM_EVENT_CONTEXT_VERSION,
                                  .page_flip_handler = flip_complete};
    int result = 0;
    while (running) {
        if (present_display(drm, resources->crtcs[0])) {
            result = 1;
            break;
        }
        int ready = poll(events, 2, business ? 20 : -1);
        if (!running)
            break;
        if (ready < 0) {
            perror("poll display/input");
            result = 1;
            break;
        }
        if (events[1].revents & POLLIN)
            drmHandleEvent(drm, &drm_events);
        if (business) {
            uint32_t next = *(volatile uint32_t *)screen_frame;
            if (next != generation && flock(frame_fd, LOCK_SH | LOCK_NB) == 0) {
                next = *(volatile uint32_t *)screen_frame;
                const unsigned char *source = screen_frame + FRAME_HEADER_BYTES + (next & 1) * FRAME_BYTES;
                memcpy(scroll_header, screen_frame+8+(next&1)*META_BYTES, META_BYTES);
                int left = scroll_header[17] >> 16, top = scroll_header[17] & 65535;
                int width = scroll_header[18] >> 16, height = scroll_header[18] & 65535;
                /* A skipped publication needs the complete current snapshot. */
                if (generation == 0 || next != generation+1) {
                    left = top = 0;
                    width = 800; height = 480;
                }
                for (int row = top; row < top+height; ++row)
                    memcpy(pixels+row*stride+left, source+(row*800+left)*4, width*4);
                generation = next;
                flock(frame_fd, LOCK_UN);
                damage(left, top, left+width, top+height);
                native_count = 0;
                if (down && SLIDER_LEFT-12 <= x0 && x0 <= SLIDER_RIGHT+12 && 279 <= y0 && y0 <= 339 && *(volatile uint32_t *)(screen_frame+4))
                    native_count = draw_count(x);
                if (down && scroll_active && scroll_kind == (int)scroll_header[2]) {
                    int offset = scroll_origin+y0-y;
                    if (offset < 0) offset = 0;
                    if (offset > (int)scroll_header[10]) offset = scroll_header[10];
                    if (draw_scroll(scroll_header, offset))
                        native_offset = offset;
                }
                printf("SCREEN_FRAME %u\n", generation);
                fflush(stdout);
            }
        }
        if (!(events[0].revents & POLLIN))
            continue;
        struct input_event event;
        if (read(input, &event, sizeof(event)) != sizeof(event)) {
            perror("read touch event");
            return 1;
        }
        if (event.type == EV_ABS && event.code == ABS_X)
            x = event.value;
        if (event.type == EV_ABS && event.code == ABS_Y)
            y = event.value;
        if (event.type == EV_KEY && event.code == BTN_TOUCH)
            down = event.value;
        if (event.type == EV_SYN && event.code == SYN_REPORT) {
            if (!previous_down && down) {
                x0 = x;
                y0 = y;
                native_draw_updates = 0;
                native_draw_ns = native_draw_max = 0;
                scroll_active = 0;
                scroll_kind = scroll_header[2];
                scroll_origin = scroll_header[9];
                native_offset = -1;
                scroll_draw_updates = 0;
                scroll_draw_ns = scroll_draw_max = 0;
            }
            if (business && down && SLIDER_LEFT-12 <= x0 && x0 <= SLIDER_RIGHT+12 && 279 <= y0 && y0 <= 339
                && *(volatile uint32_t *)(screen_frame+4)) {
                int bounded_x = x < SLIDER_LEFT ? SLIDER_LEFT : (x > SLIDER_RIGHT ? SLIDER_RIGHT : x);
                int count = 1 + ((bounded_x-SLIDER_LEFT)*399+SLIDER_WIDTH/2)/SLIDER_WIDTH;
                if (native_count != count)
                    native_count = draw_count(x);
            }
            if (business && down) {
                if (scroll_kind == (int)scroll_header[2] && scroll_header[10]
                    && scroll_hit(scroll_header, x0, y0)
                    && (scroll_active || (abs(y0-y)>=3 && abs(y0-y)>1.5*abs(x0-x)))) {
                    scroll_active = 1;
                    int offset = scroll_origin+y0-y;
                    if (offset < 0) offset = 0;
                    if (offset > (int)scroll_header[10]) offset = scroll_header[10];
                    if (offset != native_offset) {
                        if (draw_scroll(scroll_header, offset))
                            native_offset = offset;
                    }
                }
                struct timespec now;
                clock_gettime(CLOCK_MONOTONIC, &now);
                int64_t elapsed = (int64_t)(now.tv_sec-drag_last.tv_sec)*1000000000LL
                                  + now.tv_nsec-drag_last.tv_nsec;
                if (elapsed >= 33000000LL) {
                    pthread_mutex_lock(&drag_lock);
                    drag_x = x; drag_y = y; drag_x0 = x0; drag_y0 = y0;
                    drag_pending = 1;
                    pthread_cond_signal(&drag_ready);
                    pthread_mutex_unlock(&drag_lock);
                    drag_last = now;
                }
            }
            if (previous_down && !down) {
                if (business) {
                    pthread_mutex_lock(&drag_lock);
                    drag_pending = 0;
                    while (drag_busy)
                        pthread_cond_wait(&drag_ready, &drag_lock);
                    pthread_mutex_unlock(&drag_lock);
                    screen_touch(controller_port, x, y, x0, y0, 0);
                    if (native_draw_updates) {
                        printf("NATIVE_DRAG updates=%u mean_us=%llu max_us=%llu\n",
                               native_draw_updates,
                               (unsigned long long)(native_draw_ns/native_draw_updates/1000),
                               (unsigned long long)(native_draw_max/1000));
                        fflush(stdout);
                    }
                    if (scroll_draw_updates) {
                        printf("NATIVE_SCROLL updates=%u mean_us=%llu max_us=%llu\n", scroll_draw_updates,
                               (unsigned long long)(scroll_draw_ns/scroll_draw_updates/1000),
                               (unsigned long long)(scroll_draw_max/1000));
                        fflush(stdout);
                    }
                } else for (int i = 0; i < 6; ++i) {
                    int bx = 20 + (i % 3) * 260, by = 20 + (i / 3) * 240;
                    if (x >= bx && x < bx + 240 && y >= by && y < by + 200) {
                        button(i, ++counts[i]);
                        printf("TOUCH_BUTTON %d count=%u x=%d y=%d\n", i + 1, counts[i], x, y);
                        fflush(stdout);
                    }
                }
            }
            previous_down = down;
        }
    }
    if (business) {
        pthread_mutex_lock(&drag_lock);
        drag_stop = 1;
        pthread_cond_signal(&drag_ready);
        pthread_mutex_unlock(&drag_lock);
        pthread_join(drag_thread, NULL);
    }
    if (display.pending) {
        struct pollfd pending = {.fd = drm, .events = POLLIN};
        if (poll(&pending, 1, 1000) > 0)
            drmHandleEvent(drm, &drm_events);
    }
    drmModeSetCrtc(drm, resources->crtcs[0], 0, 0, 0, NULL, 0, NULL);
    for (int i = 0; i < 2; ++i) {
        struct display_buffer *buffer = &display.buffers[i];
        drmModeRmFB(drm, buffer->framebuffer);
        munmap(buffer->mapping, buffer->create.size);
        struct drm_mode_destroy_dumb destroy = {.handle = buffer->create.handle};
        ioctl(drm, DRM_IOCTL_MODE_DESTROY_DUMB, &destroy);
    }
    free(pixels);
    printf("LCD_KMS_STATS flips=%llu full_copies=%llu partial_copies=%llu copied_bytes=%llu\n",
           (unsigned long long)display.flips, (unsigned long long)display.full_copies,
           (unsigned long long)display.partial_copies, (unsigned long long)display.copied_bytes);
    drmModeFreeConnector(connector);
    drmModeFreeResources(resources);
    close(input);
    close(drm);
    if (business) {
        munmap(screen_frame, FRAME_MAP_BYTES);
        close(frame_fd);
        munmap(count_cards, CARDS_MAP_BYTES);
        close(cards_fd);
        munmap(scroll_data, scroll_map_bytes);
        close(scroll_fd);
    }
    puts("LCD_KMS_STOPPED");
    return result;
}
