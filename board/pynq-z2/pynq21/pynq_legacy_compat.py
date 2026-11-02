"""Process-local adapter for the board's 2018 PYNQ/Xlnk stack. No system edits."""
import os, time, json
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np

ENVIRONMENT = {}
DMA_OBJECTS = []
_allocator = None
_pynq = None
_bitstream_class = None

class LegacyBuffer(np.ndarray):
    def __array_finalize__(self, source):
        self._allocation = getattr(source, '_allocation', None)

    @property
    def physical_address(self):
        state = self._allocation
        if state is None or state['freed']:
            raise RuntimeError('CMA allocation is not live')
        return int(state['owner'].physical_address)+(int(self.ctypes.data)-state['virtual'])

    def flush(self):
        self._allocation['owner'].flush()

    def invalidate(self):
        self._allocation['owner'].invalidate()

    def freebuffer(self):
        state = self._allocation
        if state is not None and not state['freed']:
            state['owner'].freebuffer()
            state['freed'] = True

def allocate(shape, dtype=np.uint32, **kwargs):
    # Non-cacheable CMA avoids old slice-metadata/cache-coherency problems.
    owner = _allocator.cma_array(shape=shape, dtype=dtype, cacheable=0)
    array = np.asarray(owner).view(LegacyBuffer)
    array._allocation = dict(owner=owner, virtual=int(owner.ctypes.data), freed=False)
    return array

def clock_snapshot():
    slcr = _pynq.MMIO(0xf8000000, 0x300)
    offsets = dict(ARM_PLL_CTRL=0x100, DDR_PLL_CTRL=0x104, IO_PLL_CTRL=0x108,
                   PLL_STATUS=0x10c, ARM_CLK_CTRL=0x120, DDR_CLK_CTRL=0x124,
                   FPGA0_CLK_CTRL=0x170, FPGA1_CLK_CTRL=0x180,
                   FPGA2_CLK_CTRL=0x190, FPGA3_CLK_CTRL=0x1a0, BOOT_MODE=0x25c)
    regs = {name: int(slcr.read(offset)) for name, offset in offsets.items()}
    control = regs['FPGA0_CLK_CTRL']
    source = (control >> 4) & 3
    pll = ('IO_PLL_CTRL', 'IO_PLL_CTRL', 'ARM_PLL_CTRL', 'DDR_PLL_CTRL')[source]
    d0, d1 = (control >> 8) & 63, (control >> 20) & 63
    if not d0 or not d1:
        raise RuntimeError('FCLK0 has invalid divider')
    mhz = 50.0*((regs[pll] >> 12) & 127)/(d0*d1)
    reported = float(_pynq.Clocks.fclk0_mhz)
    if abs(mhz-100.0) > 1.0 or abs(reported-mhz) > mhz*0.01:
        raise RuntimeError('FCLK0 is not physically verified at 100 MHz: %s/%s' % (mhz, reported))
    return dict(registers=regs, fclk0_mhz=mhz, pynq_fclk0_mhz=reported,
                source=pll, divider0=d0, divider1=d1, changed_clock=False)

class IP:
    def __init__(self, address):
        self.mmio = _pynq.MMIO(address, 0x1000)
    def read(self, offset): return int(self.mmio.read(offset))
    def write(self, offset, value): self.mmio.write(offset, int(value))

class Channel:
    def __init__(self, dma, receive):
        self.dma, self.receive = dma, receive
        self.ctrl = 0x30 if receive else 0x00
        self.status = self.ctrl+4
        self.address = 0x48 if receive else 0x18
        self.length = 0x58 if receive else 0x28
        self._max_size = (1 << 23)-1
        self.first = True
        dma.mmio.write(self.ctrl, 1)
        deadline = time.monotonic()+3
        while int(dma.mmio.read(self.status)) & 1:
            if time.monotonic() > deadline: raise TimeoutError('DMA did not start')

    def transfer(self, buffer, nbytes=None):
        status = int(self.dma.mmio.read(self.status))
        if status & 0x70 or status & 1:
            raise RuntimeError('DMA invalid state: %#x' % status)
        if not self.first and not status & 2:
            raise RuntimeError('DMA channel already busy')
        size = int(buffer.nbytes if nbytes is None else nbytes)
        if not 0 < size <= min(int(buffer.nbytes), self._max_size):
            raise ValueError('Invalid DMA transfer byte count')
        if not buffer.flags.c_contiguous: raise ValueError('DMA requires contiguous buffer')
        address = int(buffer.physical_address)
        if address & 3 or size & 3: raise ValueError('DMA requires 32-bit alignment')
        if not self.receive: buffer.flush()
        self.dma.mmio.write(self.address, address)
        self.dma.mmio.write(self.address+4, 0)
        self.dma.mmio.write(self.length, size)
        self.first = False

class DMA:
    def __init__(self):
        self.mmio = _pynq.MMIO(0x41e00000, 0x1000)
        for offset in (0x00, 0x30):
            if int(self.mmio.read(offset)) & 5:
                raise RuntimeError('DMA not fresh/idle before construction')
        DMA_OBJECTS.append(self)
        self.sendchannel = Channel(self, False)
        self.recvchannel = Channel(self, True)

    def stop_safely(self, reset=False):
        self.mmio.write(0x00, 4 if reset else 0)
        if not reset: self.mmio.write(0x30, 0)
        deadline = time.monotonic()+5
        while True:
            controls = (int(self.mmio.read(0)), int(self.mmio.read(0x30)))
            statuses = (int(self.mmio.read(4)), int(self.mmio.read(0x34)))
            if not any(c & 4 for c in controls) and all(s & 1 for s in statuses):
                return True
            if time.monotonic() > deadline: return False

class LegacyOverlay:
    def __init__(self, bitfile, download=True):
        bit = Path(bitfile).resolve()
        ps = {}
        modules = ET.parse(str(bit.with_suffix('.hwh'))).findall('.//MODULE')
        for module in modules:
            if module.attrib.get('INSTANCE') == 'processing_system7_0':
                ps = {n.attrib['NAME']: n.attrib.get('VALUE') for n in module.findall('./PARAMETERS/PARAMETER')}
        if float(ps.get('PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ', 0)) != 100:
            raise RuntimeError('HWH clock target does not match routed design')
        before = clock_snapshot()
        previous = str(_pynq.PL.bitfile_name)
        if download:
            # Read only mapped, known old-overlay DMA addresses before replacing PL.
            for name, desc in _pynq.PL.ip_dict.items():
                kind = str(desc.get('type', ''))
                if 'axi_dma:' in kind or 'axi_vdma:' in kind:
                    mapped = _pynq.MMIO(int(desc['phys_addr']), 0x100)
                    if any(int(mapped.read(offset)) & 1 for offset in (0, 0x30)):
                        raise RuntimeError('Old overlay DMA still running: '+name)
            _bitstream_class(str(bit)).download()
        elif str(Path(previous).resolve()) != str(bit):
            raise RuntimeError('Reuse requested but tracked active bitstream differs')
        after = clock_snapshot()
        protected = [k for k in before['registers'] if k != 'PLL_STATUS']
        if any(before['registers'][k] != after['registers'][k] for k in protected):
            raise RuntimeError('PS clock/register state changed unexpectedly')
        self.cnn_axis_accel_0 = IP(0x40000000)
        version, status = self.cnn_axis_accel_0.read(0x28), self.cnn_axis_accel_0.read(0x24)
        if version != 0x10004 or status != 0:
            raise RuntimeError('Unexpected v1.4 hardware status: %#x/%#x' % (version, status))
        self._dma = None
        self.bitfile_name = str(bit)
        ENVIRONMENT.update(old_bit=previous, bit=str(bit), version=version,
                           clock_before=before, clock_after=after, downloaded=bool(download),
                           adapter='PYNQ2018_Xlnk_noncacheable_simpleDMA',
                           physical_addresses_from_paired_hwh=[0x40000000,0x41e00000])

    @property
    def axi_dma_0(self):
        if self._dma is None: self._dma = DMA()
        return self._dma

def install(confirm_download=False):
    global _allocator, _pynq, _bitstream_class
    if not confirm_download: raise PermissionError('Human confirmation required before hardware use')
    if os.geteuid() != 0: raise PermissionError('PYNQ hardware access requires sudo')
    import pynq
    if hasattr(pynq, 'allocate'):
        raise RuntimeError('This adapter is only for the inspected legacy Xlnk board')
    _pynq = pynq
    _allocator = pynq.Xlnk()
    _bitstream_class = pynq.Bitstream
    pynq.allocate = allocate
    pynq.Overlay = LegacyOverlay
    return ENVIRONMENT

def install_numpy_compat():
    import tifffile
    original_imread = tifffile.imread
    def imread(file, *args, **kwargs):
        return original_imread(str(file) if isinstance(file, Path) else file, *args, **kwargs)
    tifffile.imread = imread
    original_pad = np.pad
    def pad(array, pad_width, mode='constant', **kwargs):
        return original_pad(array, pad_width, mode, **kwargs)
    np.pad = pad
    # NumPy 1.14 has no little-bitorder argument. Byte-wise bit reversal after
    # its standard pack is exactly NumPy's newer little-order definition.
    original_packbits = np.packbits
    reverse = np.array([int(format(i, '08b')[::-1], 2) for i in range(256)], dtype=np.uint8)
    def packbits(array, axis=None, bitorder='big'):
        if bitorder not in ('big', 'little'): raise ValueError('Invalid bitorder')
        packed = original_packbits(array, axis=axis)
        return reverse[packed] if bitorder=='little' else packed
    np.packbits = packbits
    # Older NumPy file APIs predate consistent os.PathLike support.
    for name in ('fromfile', 'save', 'load'):
        original = getattr(np, name)
        def wrap(file, *args, _original=original, **kwargs):
            return _original(str(file) if isinstance(file, Path) else file, *args, **kwargs)
        setattr(np, name, wrap)
