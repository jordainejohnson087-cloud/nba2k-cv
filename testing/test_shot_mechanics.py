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
        source=source.replace('combo_run(ConsoleTapA);','test_tap=16;').replace('combo_run(ConsoleTapX);','test_tap=17;')
        source=re.sub(r'combo (?:ConsoleTapA|ConsoleTapX) \{.*?\n\}', '', source, flags=re.S)
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
int test_tap=0; int actual[32], output[32], delta; bool fresh=true; uint8 bytes[26];
int elapsed_time(){return delta;}
// GPC2 disallows implicit int/fix32 comparisons; C++ normally permits them.
struct fix32 {
 double value;
 explicit fix32(double v):value(v){}
 explicit operator bool() const{return value!=0.0;}
};
bool operator>=(fix32 a,double b){return a.value>=b;}
bool operator<=(fix32 a,double b){return a.value<=b;}
bool operator>(fix32 a,double b){return a.value>b;}
bool operator<(fix32 a,double b){return a.value<b;}
bool operator>=(fix32,int)=delete;
bool operator<=(fix32,int)=delete;
bool operator>(fix32,int)=delete;
bool operator<(fix32,int)=delete;
fix32 get_actual(int id){return fix32(actual[id]);}
void set_val(int id,int value){output[id]=value;}
bool gcv_ready(){return fresh;}
void gcv_read(uint8 offset,uint8* value){*value=bytes[offset];}
uint8 cv_byte(uint8 offset);
'''
        harness='''
int main(){
 int arm,seen,green_value,mode,event,x,ry,r3,dt,dir,deliver,version,legacy,lifetime;
 unsigned seq=0;
 while(std::cin>>arm>>seen>>green_value>>mode>>event>>x>>ry>>r3>>dt>>dir>>deliver>>version>>legacy>>lifetime){
  delta=dt; fresh=deliver; ++seq;
  bytes[0]=0;bytes[1]=77;bytes[2]=version;bytes[3]=seen;
  bytes[4]=arm;bytes[5]=green_value;bytes[6]=dir;
  bytes[7]=0;bytes[8]=45;bytes[9]=0;bytes[10]=100;
  for(int n=0;n<4;++n){bytes[11+n]=(event>>(24-8*n))&255;bytes[19+n]=(seq>>(24-8*n))&255;}
  bytes[23]=0;bytes[24]=lifetime;bytes[25]=mode;
  if(legacy){bytes[0]=84;bytes[1]=49;bytes[2]=legacy==3?0:legacy==4?3:legacy==5?4:legacy==6?5:legacy;}
  test_tap=0;
  for(int n=0;n<32;++n){actual[n]=0;output[n]=0;}
  actual[17]=x;actual[20]=ry;actual[6]=r3;actual[4]=70;actual[5]=40;
  for(int n=0;n<32;++n)output[n]=actual[n];
  tick();
  std::cout<<output[17]<<" "<<output[20]<<" "<<output[4]<<" "<<output[5]<<" "<<int(phase)<<" "<<fired_event<<" "<<blocked<<" "<<test_counter<<" "<<int(test_button)<<" "<<test_tap<<" "<<gate_word<<"\\n";
 }
}
'''
        cls.prelude=prelude;cls.harness=harness
        cls.cpp_source=prelude+source+harness
        cls.compiler=compiler
        cpp=root/'mechanics.cpp';cpp.write_text(cls.cpp_source)
        cls.binary=root/'mechanics'
        result=subprocess.run([compiler,'-std=c++11',str(cpp),'-o',str(cls.binary)],capture_output=True,text=True,timeout=30)
        if result.returncode:raise AssertionError(result.stderr)

    def test_host_shim_rejects_the_original_integer_threshold_error(self):
        root=Path(self.folder.name)
        broken=root/'integer_threshold.cpp'
        broken.write_text(self.cpp_source.replace('> 0.0','> 0'))
        result=subprocess.run([self.compiler,'-std=c++11',str(broken),'-o',str(root/'broken')],
            capture_output=True,text=True,timeout=30)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('deleted',result.stderr)

    def run_frames(self,frames,mode=1):
        rows=[]
        for frame in frames:
            values=dict(arm=1,seen=0,green=0,mode=mode,event=0,x=0,ry=0,r3=0,dt=5,direction=0,deliver=1,version=3,legacy=0,lifetime=100)
            values.update(frame)
            rows.append(' '.join(str(value) for value in values.values()))
        result=subprocess.run([str(self.binary)],input='\n'.join(rows)+'\n',capture_output=True,text=True,timeout=5,check=True)
        outputs=[list(map(int,row.split())) for row in result.stdout.splitlines()]
        self.assertEqual(len(outputs),len(frames))
        self.assertTrue(all(row[2:4]==[70,40] for row in outputs),'RT and movement changed')
        return outputs

    def test_slow_gcv_callbacks_do_not_latch_out_a_fresh_timing_event(self):
        frames=[dict(mode=1,lifetime=35)]
        frames += [dict(mode=1,lifetime=35,deliver=0) for _ in range(13)]
        frames += [dict(mode=1,lifetime=35,x=100,seen=1)]
        frames += [dict(mode=1,lifetime=35,x=100,deliver=0) for _ in range(13)]
        frames += [dict(mode=1,lifetime=35,x=100,seen=1,green=1,event=5)]
        out=self.run_frames(frames,mode=1)
        self.assertEqual(out[-1][0],0)
        self.assertEqual(out[-1][5],5)

    def test_controller_hold_can_precede_first_observed_gcv_callback(self):
        out=self.run_frames([{},dict(x=100),dict(x=100,deliver=0),
            dict(x=100,seen=1,green=1,event=5)],mode=1)
        self.assertEqual(out[-1][5],5)
        self.assertEqual(out[-1][0],0)

    def test_manual_a_x_protocol_bypasses_meter_gating_once_and_cancels_shots(self):
        out=self.run_frames([{},dict(legacy=1),dict(legacy=1),dict(legacy=3),dict(legacy=2),{}])
        self.assertEqual([r[7] for r in out],[0,1,1,1,2,2])
        self.assertEqual([r[9] for r in out],[0,16,0,0,17,0])
        self.assertTrue(out[1][6]);self.assertEqual(out[1][4],0)

    def test_timing_passes_x_then_releases_until_physical_release_without_rs(self):
        out=self.run_frames([{},dict(x=100,seen=1),dict(x=100,seen=1,green=1,event=1),
            dict(x=100,event=1),dict(x=100,seen=1,green=1,event=2),{},dict(x=100,seen=1,event=2)])
        self.assertEqual([r[0] for r in out],[0,100,0,0,0,0,100])
        self.assertTrue(all(r[1]==0 for r in out))
        self.assertEqual([r[5] for r in out],[0,0,1,1,1,1,1])

    def test_manual_release_uses_same_latched_x_override_without_ai(self):
        out=self.run_frames([{},dict(x=100),dict(x=100,legacy=4),dict(x=100,legacy=3),
            dict(x=100,arm=0),dict(arm=0),dict(x=100,arm=0)])
        self.assertEqual([r[0] for r in out],[0,100,0,0,0,0,100])
        self.assertEqual(out[2][7:9],[1,3])
        self.assertTrue(out[2][10] & 512)  # Raw X is held even though output is zero.
        self.assertTrue(out[2][10] & 256)  # Dedicated release capability.

    def test_manual_release_without_physical_hold_is_acknowledged(self):
        out=self.run_frames([{},dict(legacy=4),dict(legacy=3)])
        self.assertEqual(out[-1][7:9],[1,3])

    def test_powershell_hold_persists_across_callbacks_then_releases(self):
        out=self.run_frames([dict(legacy=5),dict(legacy=3,dt=200),dict(arm=0,dt=200),
            dict(legacy=4),dict(arm=0)])
        self.assertEqual([r[0] for r in out],[100,100,100,0,0])
        self.assertEqual(out[0][7:9],[1,4]);self.assertEqual(out[3][7:9],[2,3])

    def test_lost_manual_release_times_out_on_device_without_cv(self):
        out=self.run_frames([dict(legacy=5),dict(deliver=0,dt=2000),dict(deliver=0,dt=500),dict(deliver=0)])
        self.assertEqual([r[0] for r in out],[100,100,0,0])

    def test_original_standalone_gpc_hold_release_and_timeout(self):
        source=(Path(__file__).resolve().parents[1]/'testing/console_button_check.gpc').read_text()
        source=re.sub(r'^#.*$', '', source, flags=re.M).replace('main {','void tick() {')
        source=source.replace('combo_run(TapA);','test_tap=16;').replace('combo_run(TapX);','test_tap=17;')
        source=re.sub(r'combo (?:TapA|TapX) \{.*?\n\}', '', source, flags=re.S)
        source=re.sub(r'printf\([^;]+;', '', source)
        root=Path(self.folder.name);cpp=root/'standalone.cpp';binary=root/'standalone'
        cpp.write_text(self.prelude+'int phase=0,fired_event=0,blocked=0,test_counter=0,test_button=0,gate_word=0;\n'+source+self.harness)
        result=subprocess.run([self.compiler,'-std=c++11',str(cpp),'-o',str(binary)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        from unittest.mock import patch
        with patch.object(self,'binary',binary):
            out=self.run_frames([dict(legacy=5),dict(legacy=3,dt=500),dict(legacy=4),
                dict(x=100,legacy=3),dict(x=0,legacy=3),dict(x=100,legacy=3)])
            # A released physical button can be pressed again normally.
            self.assertEqual([r[0] for r in out],[100,100,0,100,0,100])
            out=self.run_frames([dict(legacy=5),dict(deliver=0,dt=2000),dict(deliver=0,dt=500)])
            self.assertEqual([r[0] for r in out],[100,100,0])

    def test_ai_hold_survives_neutral_callback_and_releases_on_observed_green(self):
        out=self.run_frames([{},dict(legacy=6),dict(legacy=3),dict(seen=1),
            dict(seen=1,green=1,event=8),dict(seen=1,green=1,event=8)])
        self.assertEqual([r[0] for r in out],[0,100,100,100,0,0])
        self.assertEqual(out[1][7:9],[1,5]);self.assertTrue(out[1][10]&4096)
        self.assertEqual(out[-1][5],8);self.assertFalse(out[-1][10]&4096)

    def test_ai_hold_disarm_and_link_loss_release_without_a_green_action(self):
        for stop in (dict(arm=0),dict(deliver=0,dt=300)):
            with self.subTest(stop=stop):
                out=self.run_frames([{},dict(legacy=6),stop,dict(arm=0)])
                self.assertEqual([r[0] for r in out],[0,100,0,0]);self.assertEqual(out[-1][5],0)

    def test_ai_hold_requires_armed_neutral_x_and_rejects_repeated_start(self):
        for initial in (dict(arm=0),dict(mode=0),dict(x=100)):
            out=self.run_frames([initial,dict(legacy=6,x=initial.get('x',0))])
            self.assertEqual(out[-1][7],0)
        out=self.run_frames([{},dict(legacy=6),dict(legacy=3),dict(legacy=6)])
        self.assertEqual([r[7] for r in out],[0,1,1,1])

    def test_ai_hold_times_out_with_live_frames_but_no_green(self):
        out=self.run_frames([{},dict(legacy=6)]+[dict(dt=500) for _ in range(5)])
        self.assertEqual(out[-1][0],0);self.assertEqual(out[-1][5],0)
        self.assertTrue(out[-1][10]&8192);self.assertFalse(out[-1][10]&4096)

    def test_synthetic_meter_uses_tested_command_to_release_physical_and_ai_holds(self):
        import sys,time
        from types import SimpleNamespace
        from unittest.mock import patch
        from meter_green import GreenGate,green_config,measure_green
        from run_video import estimate_fill
        from test_green import ColorTests,gcv
        fixture=ColorTests();fixture.setUp()
        for physical in (False,True):
            with self.subTest(physical=physical),patch.object(gcv,'PORT',0),patch.object(gcv,'TEST_PORT',0):
                worker=gcv.GCVWorker(1,1)
                gate=GreenGate(green_config(dict(armed=True,shot_mode='timing',max_age_ms=250)))
                frames=[{},dict(x=100)] if physical else [{},dict(legacy=6),dict(legacy=3)]
                try:
                    with patch.dict(sys.modules,gtuner=SimpleNamespace(gpc_string=lambda:None)):
                        for seq,front in enumerate((70,55,33),1):
                            image=fixture.frame(front=front);fill=estimate_fill(image,fixture.box)
                            measurement=measure_green(image,fixture.box,fill,gate.config)
                            stamp=time.time();state=dict(status='detected',confidence=.9,shot_id=1,fill=fill)
                            state['trigger']=gate.update(state,measurement,stamp)
                            worker.packet=dict(state,sent_at=stamp,seq=seq);worker.last_packet=time.monotonic()
                            payload=worker.process(None)[1]
                            if len(payload)==3:
                                self.assertEqual(payload,bytes((84,49,3)))
                                frames.append(dict(x=100 if physical else 0,legacy=4))
                            else:
                                frames.append(dict(x=100 if physical else 0,arm=payload[4],seen=payload[3],green=payload[5],
                                    event=int.from_bytes(payload[11:15],'big'),lifetime=int.from_bytes(payload[23:25],'big')))
                    frames.extend([dict(x=100 if physical else 0,legacy=3),dict(x=100 if physical else 0)])
                    out=self.run_frames(frames)
                    self.assertTrue(all(row[0]==100 for row in out[1:-3]))
                    self.assertTrue(all(row[0]==0 for row in out[-3:]))
                    self.assertEqual(out[-1][5],0)  # Native green-action path was not used.
                    self.assertEqual(out[-1][8],3)  # Actual GPC acknowledged tested RELEASE_X.
                finally:worker.__del__()

    def test_rhythm_mode_is_rejected_and_right_stick_never_changes(self):
        out=self.run_frames([dict(mode=0,ry=100),dict(mode=0,x=100,ry=100,seen=1,green=1,event=1)])
        self.assertEqual(out[-1][5],0);self.assertEqual(out[-1][0],100)
        self.assertTrue(all(r[1]==100 for r in out))

    def test_right_stick_and_click_do_not_block_x_shots(self):
        out=self.run_frames([dict(ry=100),dict(x=100,ry=-100,r3=100),
            dict(x=100,ry=-100,r3=100,seen=1,green=1,event=4)])
        self.assertEqual(out[-1][0],0);self.assertEqual(out[-1][1],-100)
        self.assertEqual(out[-1][5],4)

    def test_link_loss_before_event_requires_physical_release(self):
        out=self.run_frames([{},dict(x=100),dict(x=100,deliver=0,dt=300),
            dict(x=100,seen=1,green=1,event=1),{},dict(x=100),
            dict(x=100,seen=1,green=1,event=2)])
        self.assertTrue(out[2][6]);self.assertEqual(out[3][5],0)
        self.assertEqual(out[-1][5],2);self.assertEqual(out[-1][0],0)

    def test_disarm_or_unknown_wire_version_cannot_start_new_release(self):
        for invalid in (dict(arm=0),dict(version=2)):
            out=self.run_frames([{},dict(x=100,seen=1,green=1,event=1,**invalid)])
            self.assertEqual(out[-1][5],0);self.assertEqual(out[-1][0],100)

    def test_released_x_cannot_be_repressed_by_a_timeout_while_still_held(self):
        out=self.run_frames([{},dict(x=100),dict(x=100,seen=1,green=1,event=4),
            dict(x=100,deliver=0,dt=300),dict(x=100,arm=0),dict(arm=0),dict(x=100,arm=0)])
        self.assertEqual([r[0] for r in out],[0,100,0,0,0,0,100])
