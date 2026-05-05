import json
import sys
import os
import subprocess
import threading
from threading import Thread

dir_path = os.path.dirname(os.path.abspath(__file__))

sys.path.append(dir_path)

from utils import remove_extra_declarations, get_module_complete, del_head_def, extract_port_name, check_empty, get_module_head, remove_comment

from prompts import *

BENCH = 'Human'
# BENCH = 'Machine'

bench = BENCH.lower()

file_n = f'test_vv_{bench}.sv'

bar = 1
times = 10


lock = threading.Lock()

def run_main(task, description, head, idx):
    
    input_str = description + "\n Verilog Module Declaration: \n" + head  
    
    # if 'Karnaugh' in description:
    #     prompt_c = c_kmap_prompt
    # else:
    #     prompt_c = c_prompt
    
    aug_text = run_agent(aug_llm, aug_prompt, [description], lang = None)
    # print(aug_text)
    
    index_des = aug_text.find(f'Description')
    description_aug = aug_text[(index_des + 12):].strip()
    
    retry_des = 0
    ture_des_num = 0
    re_re_des = 0
    while True:
        if ture_des_num >=bar:
            break
        if retry_des >=times:
            # break
            if re_re_des >=1:
                break
            aug_text = run_agent(aug_llm, aug_prompt, [description], lang = None)
            
            index_des = aug_text.find(f'Description')
            description_aug = aug_text[(index_des + 12):].strip()
            
            retry_des = 0
            ture_des_num = 0
            re_re_des += 1
        check_des_res = run_agent(des_check_llm, des_check_prompt, [description_aug], lang = None)
        if 'yes' in check_des_res.lower():
            ture_des_num+=1
        else:
            index = check_des_res.find(f'#REASON')
            comment = check_des_res[(index + 8):]
            aug_text = run_agent(des_crc_llm, des_crc_prompt, [description_aug, comment], lang = None)
            index_des = aug_text.find(f'Description')
            description_aug = aug_text[(index_des + 12):].strip()
            retry_des += 1
    
    verilog_aug = run_agent(aug_v_llm, aug_v_prompt, [description, head, description_aug], lang = 'verilog')
    retry_aug = 0
    ture_aug_num = 0
    re_re_aug = 0
    while True:
        if ture_aug_num >=bar:
            break
        if retry_aug >=times:
            # break
            if re_re_aug >=1:
                break
            verilog_aug = run_agent(aug_v_llm, aug_v_prompt, [description, head, description_aug], lang = 'verilog')
            retry_aug = 0
            ture_aug_num = 0
            re_re_aug += 1
        check_v_res = run_agent(check_llm, check_prompt, [description_aug, verilog_aug, 'Verilog'], lang = None)
        if 'TRUE' in check_v_res:
            ture_aug_num+=1
        else:
            index = check_v_res.find(f'#REASON')
            comment = check_v_res[(index + 8):]
            verilog_aug = run_agent(crc_llm, crc_prompt, [description_aug, verilog_aug, comment, 'Verilog'], lang = 'verilog')
            retry_aug += 1
    
    generated_v = verilog_aug
    
    head = get_module_head(generated_v)
    
    completion = get_module_complete(generated_v)

    test_file = head + '\n' + completion
    
    lock.acquire()
    with open(f'./temp_{bench}/'+file_n, "w") as f:
        f.write(test_file)
        
    #  -Wall -Winfloop -Wno-timescale -g2012
    
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
            
                
        else:
            break
        
    test_file = remove_comment(head + '\n' + completion)
    
    text1 = description_aug + "\n Verilog Module Declaration: \n" + " ".join(remove_comment(head).split())

    data_dict = {"filename": task, "text1": text1, "text2": " ".join(test_file.split()), "task": "auto_aug"}
    # data.append(data_dict)
    

    lock.acquire()
    with open(f"failed_aug_{bench}_CodeQwen.jsonl", "a") as f:
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
            
with open(f"/mnt/proj73/zhpei/PlanV-2/failed_{BENCH}_CodeQwen.jsonl", "r") as f:
        failed_data = []
        for line in f:
            print(line.strip())
            failed_data.append(line.strip())

head_dict = {task["task_id"]: task["prompt"] + task["canonical_solution"] for task in head_data}

data = []
   
passed = False
 
for item in failed_data:
    task = item
    # if task != 'mt2015_muxdff':
    #     passed = True
    #     continue
    # if task == 'rule110':
    #     passed = True
    #     continue
    # if passed == False:
    #     continue
    
    for des in des_data:
        if des['task_id'] == task:
            
            description = des['detail_description']
            # description = " ".join(description.split())
            try:
                simple_description = des['simple_description']
                description = simple_description + '\n' + description
            except KeyError:
                description = description

            head = head_dict[task]
            # head = " ".join(head.split())
            
            if task != 'lemmings1':
                num = 5
            else:
                passed = True
                num = 5
                continue
            if passed == False:
                continue
            for i in range(num):
                threads = [Thread(target=run_main, args = (task, description, head, idx)) for idx in range(20)]

                for thread in threads:
                    thread.start()

                for thread in threads:
                    thread.join()

                _ = subprocess.run(['rm', '-rf', f'temp_{bench}'], close_fds=False, restore_signals=False, capture_output=True)

                os.mkdir(f'temp_{bench}')






