import os

dirs = ['zjy2','zjy5','zjy6','zjy7','zjy8','zjy9']
exts = {'py','md','json','txt','html','kt','kts','sh','bat','js','css'}
skip_dirs = {'downloads','venv','llama.cpp','__pycache__','node_modules','.git','models','lora_env','android_app','gguf_output','merged_model','lora_output','.gitignore','ollama-models'}

for d in dirs:
    total = 0
    for r, dirnames, fs in os.walk(d):
        # 跳过不需要的目录
        parts = set(r.split(os.sep))
        if parts & skip_dirs:
            continue
        for f in fs:
            ext = f.rsplit('.', 1)[-1] if '.' in f else ''
            if ext in exts:
                try:
                    with open(os.path.join(r,f), encoding='utf-8', errors='ignore') as fp:
                        total += sum(1 for _ in fp)
                except:
                    pass
    print(f'{d}: {total}')