import json
import sys
import os

dir_path = os.path.dirname(os.path.abspath(__file__))
sys.path.append(dir_path)
from utils import del_head_def

data = []

with open('/mnt/proj73/zhpei/PlanV-2/verilog-eval/data/VerilogEval_Human.jsonl', "r") as fp:
    for line in fp:
        if any(not x.isspace() for x in line):
            row = json.loads(line)
            head = row['prompt']
            
            new_head = del_head_def(head)

            row['prompt_pure'] = new_head

        data.append(row)


with open("/mnt/proj73/zhpei/PlanV-2/verilog-eval/data/VerilogEval_Human_pure.jsonl", "w") as f:
    for item in data:
        f.write(json.dumps(item) + "\n")