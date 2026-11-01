"""移出历史调试文件，只清理构建区，备份保留在包外。"""
from pathlib import Path
import shutil,sys,time
stage=Path(sys.argv[1]);backup=Path(sys.argv[2]);backup.mkdir(parents=True,exist_ok=True)
files=list(stage.iterdir())+list((stage/'www').iterdir())
for path in files:
    if '.bak' not in path.name and path.name not in ('frontend.js','auth-flow.mjs','ui-state.mjs','trap.js'):continue
    target=backup/path.relative_to(stage);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():target=target.with_name(target.name+'.'+str(time.time_ns()))
    shutil.move(str(path),str(target));print('excluded:',path.relative_to(stage))
