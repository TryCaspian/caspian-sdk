import re
import glob

def process_file(filepath):
    with open(filepath, "r") as f:
        content = f.read()
    
    lines = content.split('\n')
    new_lines = []
    
    # Very naive regex for TS class/methods
    class_re = re.compile(r'^(\s*)export\s+class\s+([A-Za-z0-9_]+)\s*(?:implements|extends|\{)')
    method_re = re.compile(r'^(\s*)(?:public\s+|private\s+|protected\s+|async\s+)*([A-Za-z0-9_]+)\s*\(.*\)\s*(?::\s*.*)?\s*\{')
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # Check if line already has a docstring above it
        has_doc = False
        if i > 0 and '*/' in lines[i-1]:
            has_doc = True
            
        if not has_doc:
            c_match = class_re.search(line)
            if c_match:
                indent = c_match.group(1)
                name = c_match.group(2)
                new_lines.append(f"{indent}/** {name} implementation. */")
                
            else:
                m_match = method_re.search(line)
                if m_match:
                    indent = m_match.group(1)
                    name = m_match.group(2)
                    if name not in ['constructor', 'if', 'while', 'for', 'switch', 'catch', 'function']:
                        new_lines.append(f"{indent}/** Execute {name}. */")
                        
        new_lines.append(line)
        i += 1
        
    with open(filepath, "w") as f:
        f.write('\n'.join(new_lines))

def main():
    for f in glob.glob("sdks/typescript/src/**/*.ts", recursive=True):
        process_file(f)

if __name__ == "__main__":
    main()
