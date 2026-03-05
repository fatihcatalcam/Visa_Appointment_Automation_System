import os

file_path = r'c:\Users\Fatih\comp-bot\bot\scraper.py'
with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1

for i in range(len(lines)):
    # Find the FIRST occurrence of the broken method
    if 'def _handle_pending_appointment(self)' in lines[i] and start_idx == -1:
        start_idx = i
    # Find the SECOND occurrence (the injected full method)
    elif 'def _handle_pending_appointment(self)' in lines[i] and start_idx != -1:
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    print(f"Deleting broken duplicate from line {start_idx+1} to {end_idx}")
    del lines[start_idx:end_idx]
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print("Success")
else:
    print("Could not find boundaries")
