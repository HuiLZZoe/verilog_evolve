import json
import sys
import os
import subprocess
import threading
from threading import Thread

dir_path = os.path.dirname(os.path.abspath(__file__))

sys.path.append(dir_path)

from utils import remove_extra_declarations, get_module_complete, del_head_def, extract_port_name, check_empty

from prompts import *

# BENCH = 'Human'
BENCH = 'Machine'

bench = BENCH.lower()

file_n = f'test_vv_{bench}.sv'

bar = 2
times = 10


lock = threading.Lock()

def run_main(task, description, head, idx):
    
    input_str = description + "\n Verilog Module Declaration: \n" + head  
    
    # if 'Karnaugh' in description:
    #     prompt_c = c_kmap_prompt
    # else:
    #     prompt_c = c_prompt
    
    key_points = run_agent(key_llm, key_prompt, [description, head], lang = None)
    
    description_key = description + '\n' + key_points
    
    prompt_c = c_prompt
    
    generated_c = run_agent(c_llm, prompt_c, [description_key, head], lang = 'c')
    generated_c_o = generated_c
    retry_c = 0
    ture_c_num = 0
    re_re_c = 0
    while True:
        if ture_c_num >=bar:
            break
        if retry_c >=times:
            # break
            if re_re_c >=1:
                break
            generated_c = run_agent(re_c_llm, re_c_prompt, [description_key, head, generated_c_o], lang = 'c')
            generated_c_o = generated_c
            retry_c = 0
            ture_c_num = 0
            re_re_c += 1
        check_c_res = run_agent(check_llm, check_prompt, [description+" But I want you to write a C implementation first, don't write Verilog.", generated_c, 'C'], lang = None)
        # print(check_c_res)
        if 'TRUE' in check_c_res:
            ture_c_num+=1
        else:
            index = check_c_res.find(f'#REASON')
            comment = check_c_res[(index + 8):]
            # print(comment)
            generated_c = run_agent(crc_llm, crc_prompt, [description+' But I want you to write a C implementation first.', generated_c, comment, 'C'], lang = 'c')
            # print(generated_c)
            retry_c += 1
    
    # print("c done")  

    generated_v = run_agent(v_llm, v_prompt, [description_key, head, generated_c], lang = 'verilog')
    generated_v_o = generated_v
    retry_v = 0
    ture_v_num = 0
    re_re_v = 0
    while True:
        if ture_v_num >=bar:
            break
        if retry_v>=times:
            if re_re_v >=1:
                break
            generated_v = run_agent(re_v_llm, re_v_prompt, [description_key, head, generated_v_o], lang = 'verilog')
            generated_v_o = generated_v
            retry_v = 0
            ture_v_num = 0
            re_re_v += 1
        check_v_res = run_agent(check_llm, check_prompt, [description, generated_v, 'Verilog'], lang = None)
        if 'TRUE' in check_v_res:
            ture_v_num+=1
        else:
            index = check_v_res.find(f'#REASON')
            comment = check_v_res[(index + 8):]
            generated_v = run_agent(crc_llm, crc_prompt, [description, generated_v, comment, 'Verilog'], lang = 'verilog')
            retry_v += 1
    
    
    completion = get_module_complete(generated_v)

    test_file = head + '\n' + completion
    
    lock.acquire()
    with open(f'./temp_{bench}/'+file_n, "w") as f:
        f.write(test_file)
    
    result = subprocess.run(['iverilog', '-Wall', '-Winfloop', '-Wno-timescale', '-g2012', '-o', f'./temp_{bench}/test_vv.out', f'./temp_{bench}/'+file_n], close_fds=False, restore_signals=False, capture_output=True, text=True)
    _ = subprocess.run(['rm', '-rf', f'./temp_{bench}/'+file_n], close_fds=False, restore_signals=False, capture_output=True)
    
    result = result.stdout + '\n' + result.stderr
    
    lock.release()
    retry_i = 0
    while True:
        if retry_i >=times:
            print(result)
            print(len(result))
            print(f'iverilog {str(retry_i)} error')
            break
        if 'error' in result:
            print(result)
            print(len(result))
            print(f'iverilog {str(retry_i)} error')
            if 'not a valid l-value' in result:
                prompt = iv_reg_prompt
                result = extract_port_name(result)
            elif 'error: Incomprehensible for loop' in result:
                prompt = iv_loop_prompt
                result = 'error: Incomprehensible for loop'
            elif 'error: Variable declaration in unnamed block' in result:
                prompt = iv_val_prompt
                result = 'error: Variable declaration in unnamed block'
            elif 'error: Invalid module instantiation' in result:
                prompt = iv_ins_prompt
                result = 'error: Invalid module instantiation'
            else:
                prompt = iv_prompt
                if len(result) >= 400:
                    response_err_sum = run_agent(err_sum_llm, err_sum_prompt, [test_file, result], lang = None)
                    result = response_err_sum
            
            generated = run_agent(iv_llm, prompt, [test_file, result], lang = 'verilog')
            completion = get_module_complete(generated)

            test_file = head + '\n' + completion
            
            lock.acquire()
            with open(f'./temp_{bench}/'+file_n, "w") as f:
                f.write(test_file)
    
            result = subprocess.run(['iverilog', '-Wall', '-Winfloop', '-Wno-timescale', '-g2012', '-o', f'./temp_{bench}/test_vv.out', f'./temp_{bench}/'+file_n], close_fds=False, restore_signals=False, capture_output=True, text=True)
            _ = subprocess.run(['rm', '-rf', f'./temp_{bench}/'+file_n], close_fds=False, restore_signals=False, capture_output=True)

            result = result.stdout + '\n' + result.stderr
            
            lock.release()
            retry_i+=1
            if retry_i == 17 and 'error' in result:
                generated = run_agent(cv_llm, cv_prompt, [input_str], lang = 'verilog')
                completion = get_module_complete(generated)
                
                test_file = head + completion
            
                lock.acquire()
                with open(f'./temp_{bench}/'+file_n, "w") as f:
                    f.write(test_file)
    
                result = subprocess.run(['iverilog', '-Wall', '-Winfloop', '-Wno-timescale', '-g2012', '-o', './temp/test_vv.out', f'./temp_{bench}/'+file_n], close_fds=False, restore_signals=False, capture_output=True, text=True)
                _ = subprocess.run(['rm', '-rf', f'./temp_{bench}/'+file_n], close_fds=False, restore_signals=False, capture_output=True)

                result = result.stdout + '\n' + result.stderr
            
                lock.release()
            
                
        else:
            break
        
    

    data_dict = {"task_id": task, "completion": completion.strip()}
    # data.append(data_dict)
    

    lock.acquire()
    with open(f"eval_sample_deepseek_coder_agent_{bench}_c12_nosimple.jsonl", "a") as f:
        f.write(json.dumps(data_dict) + "\n")
        # for item in data:
        #     f.write(json.dumps(item) + "\n")
    lock.release()
    


with open(f"/mnt/proj73/zhpei/PlanV/verilog-eval/descriptions/VerilogDescription_{BENCH}.jsonl", "r") as f:
        des_data = []
        for line in f:
            des_data.append(json.loads(line))
    
with open(f"/mnt/proj73/zhpei/PlanV/verilog-eval/data/VerilogEval_{BENCH}.jsonl", "r") as f:
        head_data = []
        for line in f:
            head_data.append(json.loads(line))

head_dict = {task["task_id"]: task["prompt"] for task in head_data}

data = []
   
passed = False
 
for des in des_data:
    task = des["task_id"] 
    # if task != 'mt2015_muxdff':
    #     passed = True
    #     continue
    # if task == 'circuit8':
    #     passed = True
    #     continue
    # if passed == False:
    #     continue
        
    description = des['detail_description']
    # description = " ".join(description.split())
    try:
        simple_description = des['simple_description']
        description = simple_description + '\n' + description
    except KeyError:
        description = description

    head = head_dict[task]
    # head = " ".join(head.split())

    threads = [Thread(target=run_main, args = (task, description, head, idx)) for idx in range(20)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    _ = subprocess.run(['rm', '-rf', f'temp_{bench}'], close_fds=False, restore_signals=False, capture_output=True)

    os.mkdir(f'temp_{bench}')






