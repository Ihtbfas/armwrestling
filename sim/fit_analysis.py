"""Bounded geometric comparison; examples are not anthropometric measurements."""
import argparse
import csv
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

MODEL_PATH = Path(__file__).with_name('arm.xml')
ELBOW_HEIGHT = .04          # Example joint center above pad contact, not measured.
HAND_OFFSET = np.array([0., 0., .06])  # Example wrist -> grip vector at zero.
FOREARM_LENGTHS = (.24, .28, .32)     # Examples only, not population percentiles.


def rotate_y(vector, angles):
    angles = np.asarray(angles, dtype=float)
    x, y, z = vector
    return np.column_stack((x * np.cos(angles) + z * np.sin(angles),
                            np.full_like(angles, y),
                            -x * np.sin(angles) + z * np.cos(angles)))


def grip_path(axis, grip0, angles):
    return np.asarray(axis) + rotate_y(np.asarray(grip0) - axis, angles)


def wrist_path(grips, angles, hand_offset, mode):
    if mode == 'fixed':
        return grips - hand_offset
    if mode == 'corotating':
        return grips - rotate_y(hand_offset, angles)
    raise ValueError('hand mode must be fixed or corotating')


def make_layouts():
    model = mujoco.MjModel.from_xml_path(str(MODEL_PATH))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    pad = data.geom('elbow_pad').xpos.copy()
    contact = pad + [0, 0, model.geom('elbow_pad').size[2]]
    joint = contact + [0, 0, ELBOW_HEIGHT]
    axis = data.xanchor[0].copy()
    grip = data.geom('handle').xpos.copy()
    layouts = []
    for name, label in [('A','A Current'), ('B','B Pad shift'),
                        ('C','C Elbow-axis'), ('D','D Offset-axis')]:
        layouts.append(dict(name=name, label=label, axis=axis.copy(), grip0=grip.copy(),
                            contact=contact.copy(), elbow=joint.copy()))
    # Move only the pad/joint; do not change the assumed human offsets.
    layouts[1]['contact'][0] += .20
    layouts[1]['elbow'][0] += .20
    # Keep the initial grip point for all candidates, isolate the trajectory change.
    layouts[2]['axis'][[0, 2]] = joint[[0, 2]]
    layouts[3]['axis'][[0, 2]] = (joint + HAND_OFFSET)[[0, 2]]
    return layouts


def layout_xml(layout):
    """Generate a simple inspection model from the same baseline XML."""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(MODEL_PATH.read_text(), parser)
    world = root.find('worldbody')
    axis = layout['axis']
    r = layout['grip0'] - axis
    fmt = lambda v: ' '.join(f'{x:.12f}' for x in v)
    for name in ('base_plate','bearing_front','bearing_rear','brake_housing','transmission_cover'):
        geom = world.find(f"geom[@name='{name}']")
        pos = np.fromstring(geom.get('pos'), sep=' ')
        pos[0] = axis[0]
        if name in ('bearing_front','bearing_rear','brake_housing'):
            size = np.fromstring(geom.get('size'), sep=' ')
            size[2] = (axis[2] - .070) / 2
            pos[2] = .070 + size[2]
            geom.set('size', fmt(size))
        if name == 'transmission_cover':
            pos[2] = axis[2]
        geom.set('pos', fmt(pos))
    for name in ('shaft','brake_endcap'):
        geom = world.find(f"geom[@name='{name}']")
        ends = np.fromstring(geom.get('fromto'), sep=' ')
        ends[[0,3]] = axis[0]
        ends[[2,5]] = axis[2]
        geom.set('fromto', fmt(ends))
    for name in ('elbow_pad','elbow_pad_base'):
        geom = world.find(f"geom[@name='{name}']")
        pos = np.fromstring(geom.get('pos'), sep=' ')
        pos[:2] = layout['contact'][:2]
        geom.set('pos', fmt(pos))
    arm = world.find("body[@name='swing_arm']")
    arm.set('pos', fmt(axis))
    back = r + [0, .08, 0]
    rod_end = back - [0, 0, .08]
    for name, side in [('arm_left',-.055),('arm_right',.055)]:
        end = rod_end + [0, side, 0]
        arm.find(f"geom[@name='{name}']").set('fromto', fmt(np.r_[[0,side,0],end]))
    arm.find("geom[@name='end_crossbar']").set('fromto', fmt(np.r_[rod_end+[0,-.075,0],rod_end+[0,.075,0]]))
    arm.find("geom[@name='grip_support']").set('fromto', fmt(np.r_[rod_end,back]))
    arm.find("geom[@name='handle']").set('pos', fmt(r))
    arm.find("geom[@name='handle_cap']").set('pos', fmt(r+[0,-.086,0]))
    # The dial follows the candidate grip's radial zero direction.
    dial = world.find("body[@name='boundary_reference']")
    dial.set('pos', fmt(axis+[0,-.175,0]))
    radial = np.array([r[0],0,r[2]])
    radial /= np.linalg.norm(radial)
    p1, p2 = radial*.043, radial*.064
    p1[1] = p2[1] = -.006
    dial.find("geom[@name='free_boundary_marker']").set('fromto', fmt(np.r_[p1,p2]))
    p1, p2 = axis+radial*.025, axis+radial*.047
    p1[1] = p2[1] = axis[1]-.183
    world.find("geom[@name='zero_tick']").set('fromto', fmt(np.r_[p1,p2]))
    ET.SubElement(world,'site',name='example_elbow_joint',type='sphere',pos=fmt(layout['elbow']),
                  size='0.008',rgba='0.0 0.65 0.55 1')
    ET.SubElement(arm,'site',name='example_wrist_corotating',type='sphere',pos=fmt(r-HAND_OFFSET),
                  size='0.008',rgba='0.7 0.2 0.65 1')
    root.insert(0, ET.Comment('Generated candidate for inspection only; not the default model.'))
    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='unicode').replace(' />','/>')+'\n'


def clearance(layout, angles):
    """Screen selected non-journal pairs, excluding intended mechanical joins."""
    if layout['name'] == 'A':
        model = mujoco.MjModel.from_xml_path(str(MODEL_PATH))
    else:
        model = mujoco.MjModel.from_xml_string(layout_xml(layout))
    data = mujoco.MjData(model)
    minima = {name:1. for name in ('arm_left','arm_right','elbow_pad','table_top')}
    moving = ('arm_left','arm_right','end_crossbar','handle','grip_support','balance_block')
    fixed = ('base_plate','bearing_front','bearing_rear','brake_housing','transmission_cover','elbow_pad','table_top')
    assembly_min = 1.
    worst = None
    axis_error = 0.
    for angle in angles:
        data.qpos[0] = angle
        mujoco.mj_forward(model, data)
        axis_error = max(axis_error, float(np.max(abs(data.geom('handle').xmat.reshape(3,3)[:,2] - [0,-1,0]))))
        for name in minima:
            value = mujoco.mj_geomDistance(model,data,model.geom('handle').id,model.geom(name).id,1.,None)
            minima[name] = min(minima[name],float(value))
        for a in moving:
            for b in fixed:
                value = float(mujoco.mj_geomDistance(model,data,model.geom(a).id,model.geom(b).id,1.,None))
                if value < assembly_min:
                    assembly_min, worst = value, [a,b,float(angle)]
    return dict(grip_minima=minima, assembly_minimum=assembly_min, worst_pair=worst,
                nq=model.nq, nu=model.nu, horizontal_axis=bool(np.allclose(model.jnt_axis[0],[0,1,0])),
                grip_axis_error_max=axis_error)


def write_csv(path, rows):
    with path.open('w',newline='') as file:
        writer = csv.DictWriter(file,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot_results(layouts, angles, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colors = ['#0072B2','#D55E00','#009E73','#AA3377']
    styles = ['-','--','-.',':']
    progress = -angles * 180 / np.pi
    with plt.rc_context({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,
                         'pdf.fonttype':42,'svg.fonttype':'none'}):
        fig, axes = plt.subplots(2,2,figsize=(11,8),layout='constrained')
        for layout,color,style in zip(layouts,colors,styles):
            path = grip_path(layout['axis'],layout['grip0'],angles)
            axes[0,0].plot(path[:,0]*100,path[:,2]*100,color=color,ls=style,label=layout['label'])
            axes[0,0].scatter(layout['axis'][0]*100,layout['axis'][2]*100,color=color,marker='x',s=45)
            axes[0,1].plot(progress,np.linalg.norm(path-layout['contact'],axis=1)*100,color=color,ls=style)
            for col,mode in enumerate(('fixed','corotating')):
                wrist = wrist_path(path,angles,HAND_OFFSET,mode)
                axes[1,col].plot(progress,np.linalg.norm(wrist-layout['elbow'],axis=1)*100,color=color,ls=style)
        # Crosses are mechanical axes; all candidates share the initial grip point.
        axes[0,0].scatter(layouts[0]['grip0'][0]*100,layouts[0]['grip0'][2]*100,color='black',marker='o',s=30)
        axes[0,0].set(xlabel='World X (cm)',ylabel='World Z (cm)',title='a  Grip trajectory (cross = mechanical axis)')
        axes[0,0].set_aspect('equal',adjustable='box')
        axes[0,0].text(.55,.91,'A/B share the same grip path',transform=axes[0,0].transAxes,fontsize=8)
        axes[0,1].set(xlabel='Downstroke angle (deg)',ylabel='Pad-contact to grip (cm)',title='b  Contact-point proxy, not forearm length')
        for col,mode in enumerate(('fixed','corotating')):
            ax = axes[1,col]
            for length in FOREARM_LENGTHS:
                ax.axhline(length*100,color='#888888',ls=(0,(2,3)),lw=.7)
            ax.set(xlabel='Downstroke angle (deg)',ylabel='Elbow-joint to wrist (cm)',
                   title=('c  Fixed world hand offset' if col==0 else 'd  Hand offset rotates with mechanism'))
            ax.set_ylim(10,45)
        for ax in axes.flat:
            ax.grid(alpha=.2)
        axes[0,1].set_ylim(10,50)
        for ax in (axes[0,1],axes[1,0],axes[1,1]):
            ax.set_xlim(0,float(progress[-1]))
        handles,labels=axes[0,0].get_legend_handles_labels()
        fig.legend(handles,labels,loc='outside lower center',ncol=4,frameon=False)
        fig.suptitle('Illustrative geometry only: joint +40 mm; hand offset +60 mm; dotted levels = 24/28/32 cm',fontsize=11)
        fig.savefig(output/'fit-comparison.png',dpi=220)
        fig.savefig(output/'fit-comparison.pdf')
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=Path('docs/analysis/fit'))
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True,exist_ok=True)
    layouts = make_layouts()
    angles = np.linspace(0,-.55,501)
    samples, summaries, sensitivities = [],[],[]
    for layout in layouts:
        path = grip_path(layout['axis'],layout['grip0'],angles)
        reference = np.linalg.norm(path-layout['contact'],axis=1)
        fixed = np.linalg.norm(wrist_path(path,angles,HAND_OFFSET,'fixed')-layout['elbow'],axis=1)
        corot = np.linalg.norm(wrist_path(path,angles,HAND_OFFSET,'corotating')-layout['elbow'],axis=1)
        summary = dict(layout=layout['name'],contact_grip_start=reference[0],contact_grip_end=reference[-1],
                       contact_grip_span=np.ptp(reference),wrist_fixed_min=min(fixed),wrist_fixed_max=max(fixed),
                       wrist_fixed_span=np.ptp(fixed),wrist_corot_span=np.ptp(corot),
                       grip_downward_drop=path[0,2]-path[-1,2],initial_grip_height=path[0,2])
        for length in FOREARM_LENGTHS:
            summary[f'max_error_L{int(length*100)}_fixed'] = max(abs(fixed-length))
        summaries.append(summary)
        for i,angle in enumerate(angles):
            samples.append(dict(layout=layout['name'],theta_rad=angle,grip_x=path[i,0],grip_y=path[i,1],
                                grip_z=path[i,2],contact_grip=reference[i],joint_wrist_fixed=fixed[i],joint_wrist_corot=corot[i]))
        cases = [('nominal',0,.04,.06),('joint_height20',0,.02,.06),('joint_height60',0,.06,.06),
                 ('joint_x_minus20',-.02,.04,.06),('joint_x_plus20',.02,.04,.06),
                 ('hand40',0,.04,.04),('hand80',0,.04,.08)]
        for case,dx,dz,hz in cases:
            # Layout geometry stays fixed: do not re-fit each candidate to the assumed anatomy.
            elbow = layout['contact'] + [dx,0,dz]
            for mode in ('fixed','corotating'):
                distance = np.linalg.norm(wrist_path(path,angles,np.array([0,0,hz]),mode)-elbow,axis=1)
                sensitivities.append(dict(layout=layout['name'],scenario=case,hand_mode=mode,
                                          length_min=min(distance),length_max=max(distance),length_span=np.ptp(distance)))
    write_csv(output/'paths.csv',samples)
    write_csv(output/'summary.csv',summaries)
    write_csv(output/'sensitivity.csv',sensitivities)
    geometry = []
    for layout in layouts:
        info = {key:(value.tolist() if isinstance(value,np.ndarray) else value) for key,value in layout.items()}
        info['clearance'] = clearance(layout,np.linspace(0,-.55,101))
        geometry.append(info)
    (output/'geometry.json').write_text(json.dumps(geometry,indent=2))
    candidate = Path(__file__).parent/'candidates/elbow_axis.xml'
    candidate.parent.mkdir(exist_ok=True)
    candidate.write_text(layout_xml(layouts[2]))
    plot_results(layouts,angles,output)
    import hashlib
    import matplotlib
    metadata = dict(model_sha256=hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest(),
                    mujoco=mujoco.__version__,numpy=np.__version__,matplotlib=matplotlib.__version__,
                    angular_samples=len(angles),angle_start=float(angles[0]),angle_end=float(angles[-1]),
                    elbow_joint_height_example=ELBOW_HEIGHT,hand_offset_example=HAND_OFFSET.tolist(),
                    forearm_length_examples=FOREARM_LENGTHS,
                    note='Deterministic model scenarios, not measured people or statistical replicates.')
    (output/'metadata.json').write_text(json.dumps(metadata,indent=2))
    for summary in summaries:
        print(summary)
    print('Candidate geometry:',geometry[2]['clearance'])


if __name__ == '__main__':
    main()
