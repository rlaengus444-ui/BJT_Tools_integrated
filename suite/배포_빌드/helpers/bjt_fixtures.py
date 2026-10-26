"""Meaningful tiny BJT fixtures. Import and call run(module, output_root) for suite parity."""
from dataclasses import replace
import hashlib, importlib.util, json, pathlib, sys, threading, time, traceback

def run(module, output_root):
    import numpy as np
    from PIL import Image
    output_root=pathlib.Path(output_root)
    output_root.mkdir(parents=True,exist_ok=True)
    inputs=output_root/'inputs';inputs.mkdir(exist_ok=True)
    for index in range(10):
        mask=np.zeros((32,36),dtype=np.uint8)
        mask[4:28,6:30]=255
        mask[11:21,13:23]=0
        if index in (3,4,5): mask[4:9,6:11]=0
        Image.fromarray(mask).save(inputs/f'layer_{index:04}.png')
    model_path=output_root/'box_input.stl'
    module.trimesh.creation.box(extents=(2.4,2.4,1.)).export(model_path)
    params=module.PipelineParams(mode='기존 슬라이스 → 격자화+재구성',input_path=inputs,output_stl=output_root/'base.stl',
        input_unit='mm',dpi_x=254.,dpi_y=254.,layer_thickness_mm=.1,slice_margin_px=2,batch_layers=2,
        shell_mm=.1,lattice_width_mm=.1,spacing_mm=.5,lattice_type='선격자',pattern='헥사고날',axes='xyz',
        shell_method='고속 정밀 EDT (정밀도 고정)',surface_mode='원본 충실형',crop_xy=True,crop_z=True,crop_margin_px=2,
        center_xy=True,workers=1,max_triangles=None,save_raw_slices=True,save_lattice_slices=True,
        intermediate_format='BMP',verify_layer_xor=False,print_equivalent=False)
    cases=[
        ('reconstruct_crop_center',dict(mode='격자 슬라이스 → 재구성')),
        ('hex_line_xyz_bmp',{}),
        ('square_line_xy_png',dict(pattern='사각',axes='xy',intermediate_format='PNG',center_xy=False,crop_xy=False,crop_z=False)),
        ('hex_face_xyz',dict(lattice_type='면격자')),
        ('shell_only',dict(lattice_type='없음(쉘만)',pattern='사각',axes='xy')),
        ('model_full',dict(mode='3D 모델 → 전체 처리',input_path=model_path,pattern='사각',axes='xyz')),
    ]
    result={'python':sys.version,'version':module.APP_VERSION,'cases':[]}
    for name,kwargs in cases:
        path=output_root/(name+'.stl')
        current=replace(params,output_stl=path,**kwargs)
        temp=output_root/(name+'_temp');temp.mkdir(exist_ok=True)
        logs=[];progress=[];previews=[]
        class Log:
            def write(self,message=''):logs.append(str(message))
            def progress(self,percent,message=''):progress.append((percent,message))
        start=time.perf_counter()
        try:
            out,raw,lattice=module.run_pipeline(current,Log(),threading.Event(),temp,lambda *a:previews.append(a))
            stats=module.binary_stl_stats(out);stats.pop('path',None)
            image_sets={}
            for label,folder in [('raw',raw),('lattice',lattice)]:
                images=[]
                for source in module.find_images(folder):
                    with Image.open(source) as image:
                        array=np.asarray(image.convert('L'))
                        images.append({'name':source.name,'size':list(image.size),'mode':image.mode,'sha256_pixels':hashlib.sha256(array.tobytes()).hexdigest()})
                image_sets[label]=images
            case={'case':name,'status':'passed','stats':stats,'images':image_sets,
                  'previews_count':len(previews),'progress_ends_at_100':bool(progress and progress[-1][0]==100.),
                  'report_status':json.loads(path.with_suffix('.run.json').read_text(encoding='utf-8'))['status']}
        except Exception as exc:
            case={'case':name,'status':'failed','error':str(exc),'traceback':traceback.format_exc(),'logs':logs}
        case['seconds']=time.perf_counter()-start
        result['cases'].append(case)
        print(name,case['status'],round(case['seconds'],3),case.get('stats',{}).get('triangles'),case.get('error',''),flush=True)
    (output_root/'fixture_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

if __name__=='__main__':
    source=pathlib.Path(r'C:\Users\Kain\PycharmProjects\shellprinting\260914_BJT_model_to_lattice_STL_all_in_one.py')
    spec=importlib.util.spec_from_file_location('_bjt_fixture_original',source)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    run(module,pathlib.Path(__file__).parent/'fixtures_original')
