import json
import sys
import os
import time
import subprocess
import threading
from threading import Thread

dir_path = os.path.dirname(os.path.abspath(__file__))

sys.path.append(dir_path)

from utils import remove_extra_declarations, get_module_complete, del_head_def

from prompts import *


lock = threading.Lock()

def run_main(task, description, head, idx):
    
    input_str = description + " The Verilog head: \n" + head  
    
    while True:
        try:
            response = myhdl_llm(myhdl_prompt.format(input_str))
            # python_code = response
            index = response.find('```python')
            endindex = response.find('```', index+9)
            python_code = response[(index + 9):(endindex)]
        except Exception as e:
            print(e)
            continue
        break
    
    lock.acquire()
    time_now = str(time.time())

    py_file = os.path.join(dir_path, 'temp', 'py_'+time_now+'.py')

    with open(py_file, "w") as f:
        f.write(python_code)
        
    lock.release()

    input_str_2 = "The verilog head:\n" + head + "\n The MyHDL code: \n" + python_code 
    
    while True:
        try:
            response = py2v_llm(py2v_prompt.format(input_str_2))
            # python_code_v = response
            index = response.find('```python')
            endindex = response.find('```', index+9)
            python_code_convert = response[(index + 9):(endindex)]
        except Exception as e:
            print(e)
            continue
        break
    
    convert_file = os.path.join(dir_path, 'temp', 'con_'+time_now+'.py')

    
    with open(convert_file, "w") as f:
        f.write(python_code_convert)
    

    lock.acquire()
    result = subprocess.run(['python', './temp/'+'con_'+time_now+'.py'], close_fds=False, restore_signals=False, capture_output=True, text=True)
    
    ex_v = os.path.exists('./temp/vv.v')
    if ex_v:
        with open('./temp/vv.v', 'r') as file:
            file_content = file.read()
            file_content = '// myhdl\n' + file_content
        _ = subprocess.run(['rm', '-rf', './temp/vv.v'], close_fds=False, restore_signals=False, capture_output=True)
    else:
        print('no vv.v')
        # print('11')
        # print(python_code_convert)
    
    lock.release()
    
    
    retry = 0
    while True:
        if retry >=3:
            break
        if result.returncode != 0:
            err = result.stderr
            while True:
                try:
                    response = err_feedback_llm_2(err_feedback_prompt_2.format(py2v_prompt.format(input_str_2), python_code_convert, err))
                except Exception as e:
                    print(e)
                    continue
                break
            # print(response)
            index = response.find('```python')
            endindex = response.find('```', index+9)
            python_code_convert = response[(index + 9):(endindex)]
            with open(convert_file, "w") as f:
                f.write(python_code_convert)
                
            lock.acquire()
            result = subprocess.run(['python', './temp/'+'con_'+time_now+'.py'], close_fds=False, restore_signals=False, capture_output=True, text=True)
    
            ex_v = os.path.exists('./temp/vv.v')
            if ex_v:
                with open('./temp/vv.v', 'r') as file:
                    file_content = file.read()
                    file_content = '// myhdl\n' + file_content
                _ = subprocess.run(['rm', '-rf', './temp/vv.v'], close_fds=False, restore_signals=False, capture_output=True)
            else:
                print(f'feed {str(retry)} no vv.v')
                # print(python_code_convert)
    
            lock.release()
            retry+=1
        else:
            break
    
    # if ex_v and file_content.strip('// myhdl\n') != '':
    #     while True:
    #         try:
    #             response = re_llm(re_prompt.format(head, file_content.strip('// myhdl\n')))
    #             # python_code_v = response
    #             index = response.find('```verilog')
    #             endindex = response.find('```', index+11)
    #             generated = response[(index + 11):(endindex)]
    #         except Exception as e:
    #             print(e)
    #             continue
    #         break
    #     file_content = generated
    
    if not ex_v:
        while True:
            try:
                response = cv_llm(cv_prompt.format(input_str))
                # python_code_v = response
                index = response.find('```verilog')
                endindex = response.find('```', index+11)
                generated = response[(index + 11):(endindex)]
            except Exception as e:
                print(e)
                continue
            break
        file_content = generated
     
    completion = get_module_complete(file_content)
    
    if completion.strip() == '':
        ex_v = False
        while True:
            try:
                response = cv_llm(cv_prompt.format(input_str))
                # python_code_v = response
                index = response.find('```verilog')
                endindex = response.find('```', index+11)
                generated = response[(index + 11):(endindex)]
            except Exception as e:
                print(e)
                continue
            break
        file_content = generated
        completion = get_module_complete(generated)
    
    
    if '// myhdl\n' in file_content:
        head_r = del_head_def(head)  
        test_file = head_r + '\n' + completion
    else:
        test_file = head + '\n' + completion
    
    lock.acquire()
    with open('./temp/test_vv.v', "w") as f:
        f.write(test_file)
    
    result = subprocess.run(['iverilog', '-o', './temp/test_vv.out', './temp/'+'test_vv.v'], close_fds=False, restore_signals=False, capture_output=True, text=True)
    _ = subprocess.run(['rm', '-rf', './temp/test_vv.v'], close_fds=False, restore_signals=False, capture_output=True)
    
    result = result.stdout + '\n' + result.stderr
    
    lock.release()
    retry_i = 0
    while True:
        if retry_i >=4:
            break
        if 'error' in result:
            print(len(result))
            print(f'iverilog {str(retry_i)} error')
            while True:
                try:
                    response = iv_llm(iv_prompt.format(test_file, result))
                    # python_code_v = response
                    index = response.find('```verilog')
                    endindex = response.find('```', index+11)
                    generated = response[(index + 11):(endindex)]
                except Exception as e:
                    print(e)
                    continue
                break
            
            file_content = generated
            completion = get_module_complete(generated)
            if ex_v:
                test_file = head_r + completion
            else:
                test_file = head + completion
            
            lock.acquire()
            with open('./temp/test_vv.v', "w") as f:
                f.write(test_file)
    
            result = subprocess.run(['iverilog', '-o', './temp/test_vv.out', './temp/'+'test_vv.v'], close_fds=False, restore_signals=False, capture_output=True, text=True)
            _ = subprocess.run(['rm', '-rf', './temp/test_vv.v'], close_fds=False, restore_signals=False, capture_output=True)

            result = result.stdout + '\n' + result.stderr
            
            lock.release()
            retry_i+=1
            if retry_i == 2 and 'error' in result:
                ex_v = False
                while True:
                    try:
                        response = cv_llm(cv_prompt.format(input_str))
                        # python_code_v = response
                        index = response.find('```verilog')
                        endindex = response.find('```', index+11)
                        generated = response[(index + 11):(endindex)]
                    except Exception as e:
                        print(e)
                        continue
                    break
                file_content = generated
                completion = get_module_complete(generated)
                
                test_file = head + completion
            
                lock.acquire()
                with open('./temp/test_vv.v', "w") as f:
                    f.write(test_file)
    
                result = subprocess.run(['iverilog', '-o', './temp/test_vv.out', './temp/'+'test_vv.v'], close_fds=False, restore_signals=False, capture_output=True, text=True)
                _ = subprocess.run(['rm', '-rf', './temp/test_vv.v'], close_fds=False, restore_signals=False, capture_output=True)

                result = result.stdout + '\n' + result.stderr
            
                lock.release()
            
                
        else:
            break
        
    
    
    if ex_v:
        completion = '// myhdl\n' + completion.strip()

    data_dict = {"task_id": task, "completion": completion.strip()}
    # data.append(data_dict)
    
    

    lock.acquire()
    with open("eval_sample_deepseek_coder_agent_human_iv.jsonl", "a") as f:
        f.write(json.dumps(data_dict) + "\n")
        # for item in data:
        #     f.write(json.dumps(item) + "\n")
    lock.release()
    


with open("/mnt/proj73/zhpei/PlanV/verilog-eval/descriptions/VerilogDescription_Human.jsonl", "r") as f:
        des_data = []
        for line in f:
            des_data.append(json.loads(line))
    
with open("/mnt/proj73/zhpei/PlanV/verilog-eval/data/VerilogEval_Human.jsonl", "r") as f:
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
    if task == 'circuit6':
        passed = True
        continue
    if passed == False:
        continue
        
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

    _ = subprocess.run(['rm', '-rf', 'temp'], close_fds=False, restore_signals=False, capture_output=True)

    os.mkdir('temp')






