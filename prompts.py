import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from utils import check_empty

API_KEY = os.environ.get("VERILOG_EVOLVE_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
BASE_URL = os.environ.get("VERILOG_EVOLVE_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com/v1"
client = OpenAI(api_key=API_KEY or "missing-api-key", base_url=BASE_URL)

client.base_url = BASE_URL

LM = os.environ.get("VERILOG_EVOLVE_MODEL", "deepseek-coder")
# LM = 'deepseek-chat'

if LM == 'deepseek-coder':
    
    TM = 1
else:
    TM = 0


def _chat_completion(**kwargs):
    if not API_KEY:
        raise RuntimeError("Set VERILOG_EVOLVE_API_KEY, OPENAI_API_KEY, or DEEPSEEK_API_KEY before calling the LLM.")
    kwargs.setdefault("timeout", float(os.environ.get("VERILOG_EVOLVE_LLM_TIMEOUT", "90")))
    max_retries = int(os.environ.get("VERILOG_EVOLVE_LLM_RETRIES", "4"))
    last_error = None
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(**kwargs)
            _log_llm_call(kwargs, response=response)
            return response
        except Exception as exc:  # noqa: BLE001 - preserve compatibility with multiple OpenAI-compatible clients.
            last_error = exc
            _log_llm_call(kwargs, error=str(exc))
            if attempt + 1 >= max_retries:
                break
            time.sleep(min(2**attempt, 30))
    raise RuntimeError(f"LLM call failed after {max_retries} retries: {last_error}") from last_error


def _log_llm_call(payload, *, response=None, error=None):
    log_dir = os.environ.get("VERILOG_EVOLVE_LLM_LOG_DIR")
    if not log_dir:
        return
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "model": payload.get("model"),
        "messages": payload.get("messages", []),
        "error": error,
    }
    if response is not None:
        record["response"] = response.choices[0].message.content if response.choices else ""
    with (path / "llm_calls.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def extract_fenced_code(response, lang=None):
    text = str(response or "").strip()
    structured = parse_structured_response(text)
    if structured.get("code"):
        return structured["code"].strip()
    if lang:
        pattern = re.compile(rf"```(?:{re.escape(lang)}|systemverilog|sv)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
    else:
        pattern = re.compile(r"```[A-Za-z0-9_+-]*\s*(.*?)```", re.DOTALL)
    matches = pattern.findall(text)
    if matches:
        return max(matches, key=len).strip()
    return text


def parse_structured_response(response):
    text = str(response or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}
    return {
        "plan": str(data.get("plan", "")),
        "code": str(data.get("code", data.get("verilog", ""))),
    }


def run_agent(agent, prompt, args, lang = None):
    max_attempts = int(os.environ.get("VERILOG_EVOLVE_AGENT_ATTEMPTS", "4"))
    formatted_prompt = prompt.format(args)
    last_error = None
    for _ in range(max_attempts):
        try:
            response = agent(formatted_prompt)
            if lang is not None:
                response = extract_fenced_code(response, lang=lang)
            if lang == 'verilog':
                if check_empty(response):
                    continue
        except Exception as e:
            last_error = e
            print(e)
            continue
        if response.strip() != '':
            return response
    raise RuntimeError(f"Agent failed to produce non-empty output after {max_attempts} attempts: {last_error}")


def structured_run_agent(agent, prompt, args):
    response = agent(prompt.format(args))
    parsed = parse_structured_response(response)
    if parsed:
        return parsed
    return {"plan": "", "code": extract_fenced_code(response)}

def myhdl_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given some descriptions about the hardware, and your job is to write the corresponding design with the MyHDL, an open-source package for using Python as a hardware description and verification language.

    In your generated code, don't include other code such as the simulation function and the top-level main function.
    And there is no need to add descriptions about the generated code, just give the code.
    Be sure to use the Same port name as in the Verilog head.
    Do not use 'in_' in the python function input in your response, use 'in'.
'''
    response = _chat_completion(
        model=LM,
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def py2v_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given code written in MyHDL, an open-source package for using Python as a hardware description and verification language.
    Your job is to wirte the corresponding conversion code in myhdl, i.e. conversion from the MyHDL code to Verilog.
    Don't directly write the Verilog!

    In your generated code, don't include other code such as the simulation function and the top-level main function.
    And there is no need to add descriptions about the generated code, just give the code.
    You must keep the same port name as in the Verilog head.
'''
    response = _chat_completion(
        model=LM,
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def err_feedback_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given an error MyHDL code, which is used to convert a MyHDL design into Verilog.
    The original Verilog head will be given as a reference to help you write the correct MyHDL code.
    And the error message will also be given to help you.
    You job is to fix the bug and return the correct MyHDL code for conversion from MyHDL to verilog.

    In your generated code, don't include other code.
    And there is no need to add descriptions about the generated code, just give the code.
'''
    response = _chat_completion(
        model=LM,
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def err_feedback_llm_2(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    In last dialog, you are asked to wirte the corresponding conversion code in myhdl, i.e. conversion from the MyHDL code to Verilog.
    However, the code you provided meets some errors.
    You will be given the error MyHDL code, which is used to convert a MyHDL design into Verilog.
    The original request will be given to you for reference.
    And the error message will also be given to help you.
    You job is to fix the bug and return the correct MyHDL code for conversion from MyHDL to verilog.

    In your generated code, don't include other code.
    And there is no need to add descriptions about the generated code, just give the code.
'''
    response = _chat_completion(
        model=LM,
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop,
        timeout=90,
    )
    response = response.choices[0].message.content
    return response

def cv_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given some descriptions about the hardware, and your job is to write the corresponding design.
    You should first generate a C program and then generate the corresponding Verilog.

    In your generated code, don't include other code.
    And there is no need to add descriptions about the generated code, just give the code.
'''
    response = _chat_completion(
        model=LM,
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def re_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given a verilog module head and a complete verilog module.
    Your job is to rewrite the verilog module, to make it use the provided verilog module head and still maintain the same functionality.

    In your generated code, don't include other code.
    And there is no need to add descriptions about the generated code, just give the code.
'''
    response = _chat_completion(
        model=LM,
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def err_sum_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    Your job is to summarize the error from iverilog.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop,
        timeout=90,
    )
    response = response.choices[0].message.content
    return response

def iv_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given a complete verilog module.
    The verilog module is processed by iverilog to check the syntax and perform basic validation of your Verilog code.
    The error message from the iverilog will be given.
    You job is to fix the bug and return the correct verilog code based on the feedback.

    In your generated code, don't include other code.
    And there is no need to add descriptions about the generated code, just give the code.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def key_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on analyzing hardware description.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def aug_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given a harward description, and your job is to write another new one in the same topic with different description.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=4096,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def aug_v_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given a harward description, and your job is to write the corresponding Verilog implementation.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=4096,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def des_check_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=4096,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def des_crc_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=4096,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def c_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given some descriptions about the hardware, and your job is to write the C implementation of the design.

    In your generated code, don't include other code.
    And there is no need to add descriptions about the generated code, just give the code.
'''
    response = _chat_completion(
        model=LM,
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def v_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given some descriptions about the hardware and also the C implementation, and your job is to write the corresponding Verilog implementation using the same module head.

    In your generated code, don't include other code.
    And there is no need to add descriptions about the generated code, just give the code.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def re_c_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given some descriptions about the hardware, and your job is to write a new implementation for the corresponding C code of the design.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

def re_v_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given some descriptions about the hardware, and your job is to write a new implementation for the corresponding Verilog using the same module head.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response


def check_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    You will be given some descriptions about the hardware and also the implementation of it (maybe C or Verilog), and your job is to check whether the implementation satisfies the correct functionality with the descriptions.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response


def crc_llm(prompt, stop=None):
    init_prmpt = f'''You are an expert on writing hardware description language.
    Your job is to correct an implementation code (in C or verilog) based on an expert's comments.
'''
    response = _chat_completion(
        model='deepseek-chat',
        messages=[
            {"role": "system", "content": init_prmpt},
            {"role": "user", "content": prompt}
        ],
        temperature=TM,
        max_tokens=2048,
        top_p=1,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        stop=stop
    )
    response = response.choices[0].message.content
    return response

myhdl_prompt = '''Write a MyHDL code to successfully fulfill the user's hardware design requirements.
Be Sure to follow the MyHDL style.
Be Sure that it is convertible to Verilog.
Be Sure to import the necessary packages.
Don't include other code such as the simulation function and the top-level main function.
A verilog module head will be given, you need to write the MyHDL code with same definition.
There is no need to assign the bit width in MyHDL code.
Be Sure to use the same port name in the MyHDL code as in the verilog head.
Set the argument one by one, don't just use one line to assign it, for example 'A, B, C, D, E, F = [intbv(i)[3:] for i in range(6)]' will result in error.
Do not use 'in_' in the python function input in your response, use 'in'.

Here is the new request:
{0[0]}

Based on the above requirements, write the correct MyHDL code. 
Be sure to use the Same port name as in the Verilog head.
Do not use 'in_' in the python function input in your response, use 'in'.
'''

py2v_prompt = '''Write a conversion code that is used to convert a MyHDL code into Verilog.
Be Sure to follow the MyHDL style.
Be Sure to include the given MyHDL code in the same file.
Be Sure to write a concise and correct code.
Be Sure to add all the required packages/libraries, use 'from myhdl import *'
Don't directly write the Verilog!
reset argument should be a ResetSignal(val, active, isasync)
When converting, assign with the fixed path './temp' and fixed name 'vv', i.e. module_inst.convert(hdl='Verilog', path='./temp', name='vv') 
Don't use the 'toVerilog' function, use 'module_inst.convert(hdl='Verilog', path='./temp', name='module') ' for converting.
When elaborating the instance, set the argument one by one, don't just use one line to assign it, for example 'A, B, C, D, E, F = [intbv(i)[3:] for i in range(6)]' will result in error.
A verilog module head will be given, Be Sure to assign the same bit width for each argument as in the verilog module head when elaborating the instance.

Here is an example code:

# Firstly, import the packages:

from myhdl import *

# Secondly, add the given MyHDL code:

@block
def inc(count, enable, clock, reset)
    
    @always_seq(clock.posedge, reset=reset)
    def seq():
        if enable:
            count.next = count + 1

    return seq
    
# Thirdly, elaborate the instance, use the same bit width as in the Verilog head:

count = Signal(modbv(0)[8:])
enable = Signal(bool(0))
clock  = Signal(bool(0))
reset = ResetSignal(0, active=0, isasync=True)

module_inst = inc(count, enable, clock, reset)

# Finally, convert to Verilog:

module_inst.convert(hdl='Verilog', path='./temp', name='vv') 


Here is the new request:
{0[0]}

Based on the above requirements, write the conversion code. 
'''

# or toVerilog(func, [,*args], directory='./temp', name='inc_{idx}')

cv_prompt = '''According to the user's hardware design requirements, please first generate a C program, and then generate the Verilog.
Don't include other code.
A verilog module head will be given, you need to first write the corresponding C code with same definition, then you generate the Verilog.
Be sure to give the complete Verilog program, i.e. adding the module head together with the completion.

Here is the new request:
{0[0]}



Based on the above requirements, write the code, first generate a C program, and then generate the Verilog.
'''

re_prompt = '''I will give you a verilog module head and a complete verilog module.
Please help me to rewrite the verilog module, to make it use the provided verilog module head and still maintain the same functionality.

The verilog module head:
{0[0]}

The complete verilog module:
{0[1]}

Based on the above requirements, generate the revised Verilog.
'''

err_feedback_prompt = '''The following MyHDL code, a code for conversion from MyHDL to Verilog, has bugs:
{0[0]}

The original Verilog head is as follows for your reference:
{0[1]}

The error message is:
{0[2]}

Please fix the bug and give the correct MyHDL code.
'''

err_feedback_prompt_2 = ''' In last dialog, I ask you to write a conversion code that is used to convert a MyHDL code into Verilog.
# The original request is as follows:
{0[0]}

# The code that you given that has bugs is as follows:
{0[1]}

# The error message is:
{0[2]}

Please help me to fix the bug and give the correct MyHDL code.
Be Sure to follow the requriments in the original request.
Be Sure to maintain the other correct parts in the last generated code.
If the bug is in the original MyHDL code, please directly revise it and return the new conversion code.
You Only need to write the correct conversion code and don't include others.
'''

iv_prompt = '''I use the iverilog to check the syntax and perform basic validation of the following Verilog code:
{0[0]}

# The error messages:
{0[1]}

Help me to fix the bug and return the correct verilog code based on the iverilog feedback. 
Be Sure to maintain the same original Verilog Module Declaration.
'''

iv_reg_prompt = '''The following verilog has the error to assign a output port inside an always block:
{0[0]}

The error is on the output port:
{0[1]}

Try to fix it by newly declare a 'reg' type of the output port, which can be assigned value inside the always block.
Finally assign the 'reg' to the original output port.
For example, if the error is on the output port 'out', you should set 'reg out_reg' first and then assign the 'out_reg' inside an always block, and finally outside the always block assign out with out_reg.
Be Sure to maintain the same original Verilog Module Declaration.
'''

iv_loop_prompt = '''The following verilog has the error to initialize the loop variables:
{0[0]}

Try to fix it by newly declaring loop variables at the beginning of the module.
For example, if the error is on the for loop with variable 'i', you should set 'integer i' first before the for loop.
Be Sure to maintain the same original Verilog Module Declaration.
'''

iv_val_prompt = '''The following verilog has the error to declare the variable inside the unnamed block:
{0[0]}

Try to fix it by moving the varibable declaration outside the always block.
For example, you must declare 'integer i;\n    always @(posedge clk) begin', where 'integer i;' is outside the always block.
Be Sure to maintain the same original Verilog Module Declaration.
'''

iv_ins_prompt = '''The following verilog has the error for invalid module instantiation:
{0[0]}

Try to fix it by stopping using 'typedef enum' in the code.
And don't claim instantiation inside an always block.
Be Sure to maintain the same original Verilog Module Declaration.
'''

c_kmap_prompt = '''The following are some hardware descriptions:
{0[0]}

The Verilog Module Declaration is:
{0[1]}

If this problem is about Karnaugh map, you should know that the K-map is in a textual tabular formats:
A 3-variable Karnaugh map is arranged in a 4x2 table.
The Columns: Label columns with the values of the first variable (e.g., a);
The Rows: Label rows with combinations of the other variables (e.g., b and c).

A 4-variable Karnaugh map is arranged in a 4x4 table;
The Columns and Rows are all combinations of two varibales.

Each cell in the table represents a minterm.

I want you to write a C implementation first.
'''

key_prompt = '''With the following descriptions and requirements, please give some key points that needed to be noticed and obeyed:
{0[0]}
The Verilog Module Declaration is:
{0[1]}

Don't add extra texts.
keep the answer short and concise.
'''

aug_prompt = '''With the following description, please write another new one in the same topic and style but with different description.

The original description:
{0[0]}

Don't add extra texts.
Don't mention the original description in your output description.
keep the answer short, concise and correct.
Give your output description under '#Description'.
'''

aug_v_prompt = '''You are required to write Verilog.

Here is an example.

Example Problem description:
{0[0]}

Example corresponding Verilog solution:
{0[1]}

Now there is a new request, please write the corresponding Verilog:
{0[2]}

Don't add extra texts.
'''


des_check_prompt = '''Whether the following problem is correct, just give yes or no, and then give brief reason under '#REASON':
{0[0]}
'''

des_crc_prompt = '''Help me to correct the following problem description based on an expert's comments.

Problem description:
{0[0]}

The comments:
{0[1]}

Return the revised problem description under '#Description'.
Don't add extra text in your response.
keep the answer short, concise and correct.
'''


c_prompt = '''The following are some hardware descriptions:
{0[0]}

The Verilog Module Declaration is:
{0[1]}

I want you to write a C implementation first.
'''

v_prompt = '''The following are some hardware descriptions:
{0[0]}

The Verilog Module Declaration is:
{0[1]}

There is a C implemetation of this design:
{0[2]}

I want you to write the corresponding complete verilog implementation, with the above Verilog Module Declaration in the code.
Don't instantiate the module.
'''

re_c_prompt = '''The following are some hardware descriptions:
{0[0]}

The Verilog Module Declaration is:
{0[1]}

There is a C implemetation of this design, but it is prone to bugs:
{0[2]}

I want you to write a new C implementation, which can be more straight-forward.
'''

re_v_prompt = '''The following are some hardware descriptions:
{0[0]}

The Verilog Module Declaration is:
{0[1]}

There is a Verilog implemetation of this design, but it is prone to bugs:
{0[2]}

I want you to write a new verilog implementation, which can be more straight-forward, with the above Verilog Module Declaration in the code.
Don't instantiate the module.
'''

err_sum_prompt = '''I use the iverilog to check the syntax and perform basic validation of the following Verilog code:
{0[0]}

First describe the following errors and then summarize them:
{0[1]}

Don't list all the errors again but summarize them. Also give a possible solution.
Keep the answer short and concise. Use just one paragraph.
'''

check_prompt = '''Help me to check whether the following {0[2]} code follows the descriptions.

The descriptions:
{0[0]}

The {0[2]} code:
{0[1]}

If the {0[2]} code is correct, output 'TRUE'. 
If it is wrong, output 'FALSE'. 
Then giving your reason under '#REASON'.
Don't add extra text in your response.
'''

crc_prompt = '''Help me to correct an hardware implementation code in {0[3]} implementation based on an expert's comments.

Hardware descriptions:
{0[0]}

The code:
{0[1]}

The comments:
{0[2]}

Return the complete revised code in {0[3]} implementation.
Don't add extra text in your response.
'''

result_repair_prompt = '''You are improving a Verilog solution using only tool-grounded feedback.

Hardware descriptions:
{0[0]}

Required Verilog Module Declaration:
{0[1]}

Current Verilog implementation or empty string for initial generation:
{0[2]}

Structured evaluation feedback from iverilog/vvp:
{0[3]}

Reusable skill guidance:
{0[4]}

Task:
- Prefer returning a JSON object with keys `plan` and `code`, where `plan` briefly states the repair/optimization approach and `code` contains one complete Verilog module.
- If JSON is not possible, return one complete Verilog module that uses exactly the required module declaration.
- Preserve the public ports, port widths, clock/reset polarity, and sequential behavior implied by the description.
- If the feedback reports mismatches, reason about functional behavior instead of only fixing syntax.
- If the feedback reports compile errors, fix the compile issue while preserving intended functionality.
- Do not add testbench code or markdown.
'''

'error: Variable declaration'
'error: Invalid module instantiation'
'error: reg q; cannot be driven by primitives or continuous assignment.'
