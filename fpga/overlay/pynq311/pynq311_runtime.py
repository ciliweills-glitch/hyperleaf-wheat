"""PYNQ 3.1.1 standard Overlay/allocate backend. No legacy Xlnk monkey-patch."""
import os,time,platform
from pathlib import Path
import xml.etree.ElementTree as ET
from importlib.metadata import version

ENVIRONMENT={}
DMA_OBJECTS=[]
_pynq=None

def prepare_backend(confirm_download=False):
    global _pynq
    if not confirm_download: raise PermissionError('Explicit hardware confirmation required')
    actual=version('pynq')
    if actual!='3.1.1': raise RuntimeError('Target is PYNQ 3.1.1; installed package is '+actual)
    if platform.machine()!='armv7l': raise RuntimeError('PYNQ-Z2 requires local Zynq-7000 ARM runtime')
    if os.geteuid()!=0: raise PermissionError('Use the PYNQ Python environment with root hardware access')
    import pynq
    if not hasattr(pynq,'allocate'): raise RuntimeError('Standard pynq.allocate missing')
    _pynq=pynq
    ENVIRONMENT.update(target_pynq='3.1.1',actual_pynq=actual,backend='standard_Overlay_allocate_SimpleDMA',
                       physical_ps_reference_mhz=50.0)
    return ENVIRONMENT

def validate_hwh(bit):
    bit=Path(bit).resolve()
    if not bit.is_file() or not bit.with_suffix('.hwh').is_file(): raise FileNotFoundError('Need paired bit/hwh')
    if bit.with_suffix('.xsa').exists(): raise ValueError('Move same-stem XSA to a separate development directory')
    root=ET.parse(str(bit.with_suffix('.hwh'))).getroot()
    modules={m.attrib.get('INSTANCE'):m for m in root.findall('.//MODULE')}
    def parameters(name):
        return {n.attrib['NAME']:n.attrib.get('VALUE','') for n in modules[name].findall('./PARAMETERS/PARAMETER')}
    ps=parameters('processing_system7_0');dma=parameters('axi_dma_0')
    if float(ps['PCW_CRYSTAL_PERIPHERAL_FREQMHZ'])!=50: raise ValueError('PYNQ-Z2 physical PS reference must be 50 MHz')
    if float(ps['PCW_ACT_FPGA0_PERIPHERAL_FREQMHZ'])!=100: raise ValueError('Expected routed 100 MHz target')
    if int(dma['C_INCLUDE_SG'])!=0 or int(dma['C_SG_LENGTH_WIDTH'])!=23: raise ValueError('Expected Simple DMA with 23-bit lengths')
    if int(dma['C_M_AXIS_MM2S_TDATA_WIDTH'])!=32 or int(dma['C_S_AXIS_S2MM_TDATA_WIDTH'])!=32: raise ValueError('Expected 32-bit stream')
    for name,address in [('cnn_axis_accel_0',0x40000000),('axi_dma_0',0x41e00000)]:
        ranges=[r for r in root.findall('.//MEMRANGE') if r.attrib.get('INSTANCE')==name]
        if not any(int(r.attrib['BASEVALUE'],0)==address for r in ranges): raise ValueError('Unexpected HWH base address: '+name)
    return dict(bit=str(bit),hwh=str(bit.with_suffix('.hwh')),target_mhz=100.0,
                cnn_address=0x40000000,dma_address=0x41e00000,
                hwh_divisors=[int(ps['PCW_FCLK0_PERIPHERAL_DIVISOR0']),int(ps['PCW_FCLK0_PERIPHERAL_DIVISOR1'])])

def clock_snapshot(require_target=True):
    mmio=_pynq.MMIO(0xf8000000,0x300)
    offsets=dict(ARM_PLL_CTRL=0x100,DDR_PLL_CTRL=0x104,IO_PLL_CTRL=0x108,PLL_STATUS=0x10c,
                 ARM_CLK_CTRL=0x120,DDR_CLK_CTRL=0x124,FPGA0_CLK_CTRL=0x170,
                 FPGA1_CLK_CTRL=0x180,FPGA2_CLK_CTRL=0x190,FPGA3_CLK_CTRL=0x1a0,BOOT_MODE=0x25c)
    regs={k:int(mmio.read(v)) for k,v in offsets.items()}
    ctrl=regs['FPGA0_CLK_CTRL'];source=(ctrl>>4)&3
    pll=('IO_PLL_CTRL','IO_PLL_CTRL','ARM_PLL_CTRL','DDR_PLL_CTRL')[source]
    pll_ctrl=regs[pll]
    if pll_ctrl&0x13: raise RuntimeError('PLL reset/power-down/bypass state requires operator review')
    d0,d1=(ctrl>>8)&63,(ctrl>>20)&63
    if not d0 or not d1: raise RuntimeError('Invalid FCLK0 divider')
    source_mhz=50.0*((pll_ctrl>>12)&127)
    actual=source_mhz/(d0*d1);reported=float(_pynq.Clocks.fclk0_mhz)
    if abs(reported-actual)>actual*0.01: raise RuntimeError('PYNQ clock readback disagrees with 50MHz physical reference')
    if require_target and abs(actual-100)>1: raise RuntimeError('FCLK0 not within 1% of 100 MHz')
    return dict(registers=regs,source=pll,source_mhz=source_mhz,divider0=d0,divider1=d1,
                fclk0_mhz=actual,pynq_fclk0_mhz=reported)

def choose_divisors(source_mhz,preferred):
    if all(1<=x<64 for x in preferred) and abs(source_mhz/(preferred[0]*preferred[1])-100)<=1:
        return tuple(preferred)
    a,b=min(((a,b) for a in range(1,64) for b in range(1,64)),
            key=lambda q:(abs(source_mhz/(q[0]*q[1])-100),abs(q[0]-q[1])))
    if abs(source_mhz/(a*b)-100)>1: raise RuntimeError('FCLK0 cannot reach 100MHz within 1% without changing PLL')
    return a,b

class DMARecovery:
    def __init__(self,mmio): self.mmio=mmio
    def stop_safely(self,reset=False):
        self.mmio.write(0,4 if reset else 0)
        if not reset: self.mmio.write(0x30,0)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            controls=[int(self.mmio.read(k)) for k in (0,0x30)]
            statuses=[int(self.mmio.read(k)) for k in (4,0x34)]
            if not any(x&4 for x in controls) and all(x&1 for x in statuses): return True
            time.sleep(0.001)
        return False

def stop_old_dma():
    for name,desc in _pynq.PL.ip_dict.items():
        kind=str(desc.get('type',''))
        if 'axi_dma:' not in kind and 'axi_vdma:' not in kind: continue
        mmio=_pynq.MMIO(int(desc['phys_addr']),0x100)
        for offset in (0,0x30):
            ctrl=int(mmio.read(offset));status=int(mmio.read(offset+4))
            if ctrl&1 and ('axi_vdma:' in kind or not status&3):
                raise RuntimeError('Existing DMA still active: '+name)
        if 'axi_dma:' in kind and not DMARecovery(mmio).stop_safely():
            raise RuntimeError('Could not halt previous idle DMA: '+name)

class BoardOverlay:
    def __init__(self,bitfile,download=True):
        if _pynq is None: raise RuntimeError('prepare_backend must precede hardware access')
        spec=validate_hwh(bitfile);before=clock_snapshot(require_target=False)
        previous=str(_pynq.PL.bitfile_name)
        if download: stop_old_dma()
        elif str(Path(previous).resolve())!=spec['bit']: raise RuntimeError('Reuse requires same tracked bitstream path')
        overlay=_pynq.Overlay(spec['bit'],download=False)
        for name,address in [('cnn_axis_accel_0',spec['cnn_address']),('axi_dma_0',spec['dma_address'])]:
            if int(overlay.ip_dict[name]['phys_addr'])!=address: raise ValueError('Parsed address mismatch: '+name)
        if download:
            d0,d1=choose_divisors(before['source_mhz'],spec['hwh_divisors'])
            # Overlay.download configures clocks from this instance's clock_dict.
            # Limit that operation to FCLK0 and leave FCLK1..3 untouched.
            overlay.clock_dict={0:dict(enable=1,divisor0=d0,divisor1=d1)}
            overlay.download()
        after=clock_snapshot()
        protected=[k for k in before['registers'] if k not in ('FPGA0_CLK_CTRL','PLL_STATUS')]
        if any(before['registers'][k]!=after['registers'][k] for k in protected):
            raise RuntimeError('PLL/CPU/DDR/other PL clock registers changed unexpectedly')
        self._overlay=overlay;self.cnn_axis_accel_0=overlay.cnn_axis_accel_0
        version_value=int(self.cnn_axis_accel_0.read(0x28));status=int(self.cnn_axis_accel_0.read(0x24))
        if version_value!=0x10004 or status!=0: raise RuntimeError('Unexpected CNN version/status')
        self._dma=None
        ENVIRONMENT.update(old_bit=previous,bit=spec['bit'],downloaded=bool(download),version=version_value,
                           clock_before=before,clock_after=after,hwh=spec)
        DMA_OBJECTS.append(DMARecovery(_pynq.MMIO(spec['dma_address'],0x1000)))

    @property
    def axi_dma_0(self):
        if self._dma is None:
            self._dma=self._overlay.axi_dma_0
            if int(self._dma.buffer_max_size)!=(1<<23)-1: raise RuntimeError('DMA driver maximum length mismatch')
        return self._dma

def load_overlay(bitfile,download=True): return BoardOverlay(bitfile,download=download)
