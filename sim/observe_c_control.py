"""Observe frozen candidate C and unchanged brake law; scripted user torque only."""
import argparse
import contextlib
import csv
import hashlib
import json
from pathlib import Path
import threading
import time

import mujoco
import numpy as np

from sim.control import BrakeParameters, brake_state
from sim.run import keyboard_torque
from sim.visualization import configure_camera, set_boundary_marker, update_brake_arrow

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / 'sim/candidates/elbow_axis.xml'


def simulate(magnitude, push_until=None, push_seconds=None, viewer=None, model=None, data=None):
    """One downstroke, release, active return, then release on crossing zero."""
    parameters = BrakeParameters()
    if model is None:
        model = mujoco.MjModel.from_xml_path(str(MODEL))
        data = mujoco.MjData(model)
    rows = []
    phase, phase_start = 'push', 0.0
    wall_start = time.monotonic()
    while data.time < 25.0:
        if viewer is not None and not viewer.is_running():
            raise RuntimeError('Observation interrupted by window closure')
        with viewer.lock() if viewer is not None else contextlib.nullcontext():
            angle, speed = float(data.qpos[0]), float(data.qvel[0])
            state = brake_state(angle, speed, parameters)
            now = float(data.time)
            if phase == 'push' and ((push_until is not None and state.relative_angle >= push_until)
                                    or (push_seconds is not None and now >= push_seconds)):
                phase, phase_start = 'release', now
            elif phase == 'release' and now - phase_start >= .6:
                phase, phase_start = 'return', now
            elif phase == 'return' and state.relative_angle <= 0:
                phase, phase_start = 'coast', now
            elif phase == 'coast' and now - phase_start >= .3:
                break
            key = '1' if phase == 'push' else ('2' if phase == 'return' else '0')
            user = keyboard_torque(ord(key), magnitude, parameters)
            data.qfrc_applied[0] = user + state.applied_torque
            # Compute constraint force for this input before integration.
            mujoco.mj_forward(model, data)
            rows.append(dict(time=now, phase=phase, theta=angle, q=state.relative_angle,
                             omega=speed, q_dot=-speed, user_torque=user,
                             target=state.target_torque, brake=state.applied_torque,
                             brake_power=state.applied_torque*speed,
                             limit_torque=float(data.qfrc_constraint[0]),
                             grip_x=float(data.geom('handle').xpos[0]),
                             grip_z=float(data.geom('handle').xpos[2])))
            if viewer is not None:
                grip = data.geom('handle')
                update_brake_arrow(viewer.user_scn,grip.xpos,data.xanchor[0],state.applied_torque,
                                   data.xaxis[0],grip.xmat.reshape(3,3)[:,2])
            mujoco.mj_step(model, data)
        if viewer is not None and len(rows) % 5 == 0:
            viewer.sync()
            time.sleep(max(0., wall_start + float(data.time) - time.monotonic()))
    if phase != 'coast':
        raise RuntimeError('Scenario did not complete within 25 simulation seconds')
    return rows


def summarize(rows):
    phases = {}
    for name in ('push','release','return','coast'):
        block = [row for row in rows if row['phase'] == name]
        phases[name] = dict(start=block[0]['time'],end=block[-1]['time'],
                            q_start=block[0]['q'],q_end=block[-1]['q'],
                            speed_start=block[0]['omega'],speed_end=block[-1]['omega'])
    dt = .002
    return dict(duration=rows[-1]['time'],peak_q=max(r['q'] for r in rows),
                maximum_target=max(r['target'] for r in rows),
                maximum_brake_power=max(r['brake_power'] for r in rows),
                sampled_brake_work=sum(r['brake_power']*dt for r in rows),
                constraint_steps=sum(abs(r['limit_torque'])>1e-6 for r in rows),
                maximum_limit_torque=max(abs(r['limit_torque']) for r in rows),
                first_boundary_time=next(r['time'] for r in rows if r['q']>.25),
                phases=phases)


def write_csv(path, rows):
    with path.open('w',newline='') as file:
        writer=csv.DictWriter(file,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_plot(runs, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    with plt.rc_context({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'pdf.fonttype':42}):
        fig,axes=plt.subplots(4,2,figsize=(12,10),layout='constrained',sharex='col',sharey='row')
        for col,(name,rows) in enumerate(runs.items()):
            t=np.array([r['time'] for r in rows])
            q=np.array([r['q'] for r in rows])
            target=np.array([r['target'] for r in rows])
            brake=np.array([r['brake'] for r in rows])
            user=np.array([r['user_torque'] for r in rows])
            axes[0,col].plot(t,q,color='#0072B2',label='q = -theta')
            axes[0,col].axhline(.25,color='#999999',ls='--',lw=1,label='Free boundary')
            axes[0,col].axhline(.55,color='#AA3377',ls=':',lw=1,label='Negative travel stop')
            axes[0,col].set(title=name,ylabel='Progress q (rad)',ylim=(-.85,.65))
            axes[1,col].plot(t,target,color='#D55E00',label='Target magnitude M')
            axes[1,col].plot(t,brake,color='#0072B2',ls='--',label='Signed brake torque')
            axes[1,col].plot(t,user,color='#444444',ls=':',label='User torque')
            axes[1,col].set(ylabel='Torque (N m)',ylim=(-.65,.85))
            axes[2,col].plot(t,[r['brake_power'] for r in rows],color='#009E73',label='Brake power')
            axes[2,col].axhline(0,color='#999999',lw=.7)
            axes[2,col].set(ylabel='Brake power (W)')
            axes[3,col].plot(t,[r['limit_torque'] for r in rows],color='#AA3377',label='Limit constraint torque')
            axes[3,col].set(xlabel='Simulation time (s)',ylabel='Limit torque (N m)')
            for ax in axes[:,col]:
                ax.grid(alpha=.2)
                ax.legend(fontsize=8,loc='lower left')
            for phase,color in [('release','#EEEEEE'),('return','#E8F4EE'),('coast','#EEF1F8')]:
                group=[r for r in rows if r['phase']==phase]
                for ax in axes[:,col]:
                    ax.axvspan(group[0]['time'],group[-1]['time'],color=color,zorder=-1)
                axes[0,col].text((group[0]['time']+group[-1]['time'])/2,.60,phase,
                                  ha='center',va='top',fontsize=8,rotation=90)
        fig.suptitle('Frozen candidate C: push -> release -> active return -> release at zero',fontsize=12)
        fig.savefig(output/'control-observation.png',dpi=200)
        fig.savefig(output/'control-observation.pdf')
        plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--viewer',action='store_true',help='show the gentle cycle in real time')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'docs/analysis/control-c')
    args=parser.parse_args()
    output=args.output_dir
    output.mkdir(parents=True,exist_ok=True)
    frozen=['sim/arm.xml','sim/candidates/elbow_axis.xml','sim/control.py']
    frozen += [str(path.relative_to(ROOT)) for path in sorted((ROOT/'docs/analysis/fit').rglob('*'))
               if path.is_file()]
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in frozen}
    runs={}
    if args.viewer:
        from mujoco import viewer as mj_viewer
        model=mujoco.MjModel.from_xml_path(str(MODEL))
        set_boundary_marker(model,BrakeParameters())
        data=mujoco.MjData(model)
        before=set(threading.enumerate())
        with mj_viewer.launch_passive(model,data,show_left_ui=False,show_right_ui=False) as viewer:
            worker=next((t for t in threading.enumerate() if t not in before and t.name.endswith('(_launch_internal)')),None)
            with viewer.lock():
                configure_camera(viewer.cam)
            runs['Gentle: +/-0.15 N m']=simulate(.15,push_until=.40,viewer=viewer,model=model,data=data)
        if worker:
            worker.join(timeout=5)
    else:
        runs['Gentle: +/-0.15 N m']=simulate(.15,push_until=.40)
    runs['Default: +/-0.50 N m']=simulate(.5,push_seconds=1.)
    summaries={}
    for name,rows in runs.items():
        slug='gentle' if name.startswith('Gentle') else 'default'
        write_csv(output/f'{slug}.csv',rows)
        summaries[slug]=summarize(rows)
        assert max(r['brake_power'] for r in rows)<=1e-12
        assert all(r['target']==0 for r in rows if r['q']<=.25)
        assert summaries[slug]['phases']['return']['q_end']<.01
    assert summaries['gentle']['constraint_steps']==0
    model=mujoco.MjModel.from_xml_path(str(MODEL))
    data=mujoco.MjData(model)
    spatial=[]
    # This is an explicit kinematic lookup, not the interactive motion input.
    for q in (0,.1,.25,.35,.4,.45,.55):
        data.qpos[0]=-q
        mujoco.mj_forward(model,data)
        forward=brake_state(-q,-.2,BrakeParameters())
        backward=brake_state(-q,.2,BrakeParameters())
        spatial.append(dict(q=q,theta=-q,grip_x=float(data.geom('handle').xpos[0]),
                            grip_z=float(data.geom('handle').xpos[2]),target=forward.target_torque,
                            down_brake=forward.applied_torque,return_brake=backward.applied_torque))
    write_csv(output/'angle-map.csv',spatial)
    (output/'summary.json').write_text(json.dumps(summaries,indent=2))
    make_plot(runs,output)
    assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest for name,digest in hashes.items())
    (output/'frozen-inputs.json').write_text(json.dumps(dict(sha256=hashes,parameters=vars(BrakeParameters()),
        mujoco=mujoco.__version__,note='User torques are scripted external inputs; no controller/model changes.'),indent=2))
    print(json.dumps(summaries,indent=2))
    print('Frozen A/C/D analysis and brake code hashes unchanged.')


if __name__=='__main__':
    main()
