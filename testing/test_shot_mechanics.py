"""Execute the actual GPC main body against a host controller/GCV shim.

This checks state transitions, not the vendor compiler, USB or Xbox behavior.
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class ShotMechanicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler=shutil.which('g++')
        if not compiler:raise unittest.SkipTest('C++ host shim requires g++')
        cls.folder=tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        root=Path(cls.folder.name)
        source=(Path(__file__).resolve().parents[1]/'bridge/titan_meter.gpc').read_text()
        source=re.sub(r'^#.*$', '', source, flags=re.M)
        source=source.replace('main {','void tick() {').replace('    combo_run(Heartbeat);','')
        source=re.sub(r'combo Heartbeat \{.*?\n\}', '', source, flags=re.S)
        prelude='''
#include <cstdint>
#include <cstdlib>
#include <iostream>
using uint8=uint8_t; using uint16=uint16_t; using uint32=uint32_t;
using int8=int8_t; using int16=int16_t;
#define TRUE true
#define FALSE false
#define BUTTON_17 17
#define PS4_RY 20
#define PS4_R3 6
int actual[32], output[32], delta; bool fresh=true; uint8 bytes[26];
int elapsed_time(){return delta;}
int get_actual(int id){return actual[id];}
void set_val(int id,int value){output[id]=value;}
bool gcv_ready(){return fresh;}
void gcv_read(uint8 offset,uint8* value){*value=bytes[offset];}
uint8 cv_byte(uint8 offset);
'''
        harness='''
int main(){
 int arm,seen,green_value,mode,event,x,ry,r3,dt,dir,deliver,version;
 unsigned seq=0;
 while(std::cin>>arm>>seen>>green_value>>mode>>event>>x>>ry>>r3>>dt>>dir>>deliver>>version){
  delta=dt; fresh=deliver; ++seq;
  bytes[0]=0;bytes[1]=77;bytes[2]=version;bytes[3]=seen;
  bytes[4]=arm;bytes[5]=green_value;bytes[6]=dir;
  bytes[7]=0;bytes[8]=45;bytes[9]=0;bytes[10]=100;
  for(int n=0;n<4;++n){bytes[11+n]=(event>>(24-8*n))&255;bytes[19+n]=(seq>>(24-8*n))&255;}
  bytes[23]=0;bytes[24]=100;bytes[25]=mode;
  for(int n=0;n<32;++n){actual[n]=0;output[n]=0;}
  actual[17]=x;actual[20]=ry;actual[6]=r3;actual[4]=70;actual[5]=40;
  for(int n=0;n<32;++n)output[n]=actual[n];
  tick();
  std::cout<<output[17]<<" "<<output[20]<<" "<<output[4]<<" "<<output[5]<<" "<<int(phase)<<" "<<fired_event<<" "<<blocked<<"\\n";
 }
}
'''
        cpp=root/'mechanics.cpp';cpp.write_text(prelude+source+harness)
        cls.binary=root/'mechanics'
        result=subprocess.run([compiler,'-std=c++11',str(cpp),'-o',str(cls.binary)],capture_output=True,text=True,timeout=30)
        if result.returncode:raise AssertionError(result.stderr)

    def run_frames(self,frames,mode=0):
        rows=[]
        for frame in frames:
            values=dict(arm=1,seen=0,green=0,mode=mode,event=0,x=0,ry=0,r3=0,dt=5,direction=0,deliver=1,version=3)
            values.update(frame)
            rows.append(' '.join(str(value) for value in values.values()))
        result=subprocess.run([str(self.binary)],input='\n'.join(rows)+'\n',capture_output=True,text=True,timeout=5,check=True)
        outputs=[list(map(int,row.split())) for row in result.stdout.splitlines()]
        self.assertEqual(len(outputs),len(frames))
        self.assertTrue(all(row[2:4]==[70,40] for row in outputs),'RT and movement changed')
        return outputs

    def test_timing_passes_x_then_releases_until_physical_release_without_rs(self):
        out=self.run_frames([{},dict(x=100,seen=1),dict(x=100,seen=1,green=1,event=1),
            dict(x=100,event=1),dict(x=100,seen=1,green=1,event=2),{},dict(x=100,seen=1,event=2)],mode=1)
        self.assertEqual([r[0] for r in out],[0,100,0,0,0,0,100])
        self.assertTrue(all(r[1]==0 for r in out))
        self.assertEqual([r[5] for r in out],[0,0,1,1,1,1,1])

    def test_rhythm_flick_has_delay_duration_neutral_and_one_event_per_hold(self):
        frames=[{},dict(ry=100,seen=1)]+[dict(ry=100,seen=1,green=1,event=1) for _ in range(40)]
        out=self.run_frames(frames)
        self.assertEqual(out[1][1],100)
        flick=[i for i,r in enumerate(out) if r[1]==-100]
        self.assertTrue(flick)
        self.assertGreaterEqual((min(flick)-2)*5,45)
        self.assertEqual(len(flick)*5,100)
        self.assertEqual(out[-1][1],0)
        self.assertTrue(all(r[0]==0 for r in out))
        self.assertEqual(out[-1][5],1)

    def test_no_flick_on_x_or_early_rs_release_or_unobserved_shot(self):
        for frames in ([{},dict(x=100,seen=1),dict(x=100,seen=1,green=1,event=1)],
                       [{},dict(ry=100,seen=1),{},dict(seen=1,green=1,event=1)],
                       [{},dict(ry=100,seen=1,green=1,event=1)]):
            out=self.run_frames(frames)
            self.assertTrue(all(r[1]!=-100 and r[5]==0 for r in out))

    def test_downward_flick_requires_upward_physical_hold(self):
        out=self.run_frames([dict(direction=1),dict(direction=1,ry=-100,seen=1)]+
            [dict(direction=1,ry=-100,seen=1,green=1,event=1) for _ in range(12)])
        self.assertTrue(any(r[1]==100 for r in out))

    def test_cancel_stale_and_disarm_stop_pending_flick_and_require_release(self):
        for cancel in (dict(r3=100),dict(arm=0),dict(deliver=0,dt=150)):
            frames=[{},dict(ry=100,seen=1),dict(ry=100,seen=1,green=1,event=1),
                    dict(ry=100,seen=1,green=1,event=1,**cancel),
                    dict(ry=100,seen=1,green=1,event=2),{},dict(ry=100,seen=1),
                    dict(ry=100,seen=1,green=1,event=3)]
            out=self.run_frames(frames)
            self.assertEqual(out[3][4],0)
            self.assertTrue(out[3][6]);self.assertTrue(out[4][6])
            self.assertEqual(out[3][0],0,'R3 must not be remapped to X')
            self.assertEqual(out[-1][5],3)

    def test_mode_change_and_unknown_protocol_require_neutral_and_do_not_fire(self):
        out=self.run_frames([{},dict(ry=100,seen=1),dict(mode=1,x=100,green=1,event=1),
            dict(mode=1,x=100,seen=1),dict(mode=1),dict(mode=1,x=100,seen=1),
            dict(mode=1,x=100,seen=1,green=1,event=2)])
        self.assertTrue(out[2][6]);self.assertTrue(out[3][6]);self.assertEqual(out[-1][0],0)
        self.assertEqual(out[-1][5],2)
        for invalid in (dict(mode=9),dict(version=2)):
            out=self.run_frames([{},dict(x=100,ry=100,seen=1,green=1,event=1,**invalid)])
            self.assertEqual(out[-1][5],0);self.assertTrue(out[-1][6])
