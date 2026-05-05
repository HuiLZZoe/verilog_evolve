import re

def remove_extra_declarations(verilog_code):
    lines = verilog_code.split('\n')
    output_lines = []
    declared_ports = set()

    for line in lines:
        line = line.strip()
        keywords = ['input', 'output', 'inout', 'reg', 'wire', 'integer', 'real', 'parameter', 'localparam']
        key = False
        for keyword in keywords:
            if line.startswith(keyword):
                key = True
                port_declaration = line[len(keyword):].strip()
                if ',' in port_declaration:
                    port = port_declaration.strip(',').split()[-1]
                    if port not in declared_ports:
                        declared_ports.add(port)
                        output_lines.append(f"{keyword} {port};")
                elif ';' in port_declaration:
                    if ');' in port_declaration:
                        port = port_declaration.strip(');').split()[-1]

                        if port not in declared_ports:
                            declared_ports.add(port)
                            output_lines.append(line)
                    else:
                        port = port_declaration.strip(';').split()[-1]
                        if port not in declared_ports:
                            declared_ports.add(port)
                            output_lines.append(line)
                else:
                    port = port_declaration.split()[-1]
                    if port not in declared_ports:
                        declared_ports.add(port)
                        output_lines.append(line)
                break
        if not key:
            output_lines.append(line)
    

    return '\n'.join(output_lines)

def get_module_complete(text):
    index = text.find('module')
    text = text[index:]
    pattern = r"module\s+\w+\s*\(.*?\);"
    # match = re.search(pattern, text, re.DOTALL)
    
    result = re.sub(pattern, "", text, flags=re.DOTALL)
    lines = result.split('\n')
    output_lines = []
    keywords = ['input', 'output']
    for line in lines:
        key = False
        for keyword in keywords:
            if keyword in line:
                key = True
        if not key:       
            output_lines.append(line)
    
    result = '\n'.join(output_lines)
    
    return result

def get_module_head(text):
    
    index = text.find(f'module')
    endindex = text.find(';', index)
    response = text[index :(endindex + 1)]
    
    return  response
    
    # index = text.find('module')
    # text = text[index:]
    # pattern = r"module\s+\w+\s*\(.*?\);"
    # match = re.search(pattern, text, re.DOTALL)
    
    # if match:
    #     return match.group()
    # else:
    #     return None
    

def del_head_def(head):
    lines = head.split('\n')
    output_lines = []
    for line in lines:
        line = line.strip()
        keywords = ['input', 'output', 'inout', 'reg', 'wire', 'integer', 'real', 'parameter', 'localparam']
        key=False
        for keyword in keywords:
            if line.startswith(keyword):
                key = True
                port_declaration = line[len(keyword):].strip()
                if ',' in port_declaration:
                    port = port_declaration.strip(',').split()[-1]
                    output_lines.append(f"{port},")
                elif ';' in port_declaration:
                    if ');' in port_declaration:
                        port = port_declaration.strip(');').split()[-1]
                        output_lines.append(f"{port});")
                    else:
                        port = port_declaration.strip(';').split()[-1]
                        output_lines.append(f"{port});")
                else:
                    port = port_declaration.split()[-1]
                    output_lines.append(f"{port}")
                break
        if not key:
            output_lines.append(line)
            
    return '\n'.join(output_lines)


def extract_port_name(error_string):
    pattern = r"error: (\w+) is not a valid l-value in"
    match = re.search(pattern, error_string)
    if match:
        return match.group(1)
    else:
        return None

def check_empty(string):
    string = get_module_complete(string)
    string = string.strip('endmodule').strip()
    lines = string.split('\n')
    output_lines = []
    for line in lines:
        if line.startswith('//'):
            pass
        else:
            output_lines.append(line)
    if len(output_lines) == 0:
        return True
    else:
        return False
    
def remove_comment(string):
    init = 0
    while True:
        index = string.find('//', init)
        if index == -1:
            break
        newline_index = string.find('\n', index)
        if newline_index == -1:
            string = string[:index]
            break
        string = string[:index] + string[newline_index:]
        init = index + 1
        
    return string