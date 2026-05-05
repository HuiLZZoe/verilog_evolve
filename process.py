import json
import sys
import os

dir_path = os.path.dirname(os.path.abspath(__file__))
sys.path.append(dir_path)
from utils import remove_extra_declarations, get_module_complete

    
with open("/mnt/proj73/zhpei/PlanV-2/verilog-eval/data/VerilogEval_Human.jsonl", "r") as f:
        head_data = []
        for line in f:
            head_data.append(json.loads(line))

head_dict = {task["task_id"]: task["prompt"] for task in head_data}

data = []

with open('/mnt/proj73/zhpei/PlanV-2/eval_sample_deepseek_coder_agent_human_2.jsonl', "r") as fp:
    for line in fp:
        if any(not x.isspace() for x in line):
            row = json.loads(line)
            completion = row['completion']
            task = row["task_id"]
            head = head_dict[task]
            
            # if task not in ['counter_2bc', 'edgedetect2', 'edgedetect', 'edgecapture']:
            
            #     completion = completion.replace("in_","in")
            
            # if task in ['edgedetect2', 'edgedetect', 'edgecapture']:
            
            #     completion = completion.replace("in_","in").replace('inprev', 'in_prev')
                
            inter_v = head + completion

            new_v = remove_extra_declarations(inter_v)

            completion = get_module_complete(new_v)

            row['completion'] = completion.strip()

        data.append(row)


with open("/mnt/proj73/zhpei/PlanV-2/eval_sample_deepseek_coder_agent_human_2_t.jsonl", "w") as f:
    for item in data:
        f.write(json.dumps(item) + "\n")