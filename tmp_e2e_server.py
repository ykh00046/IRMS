from pathlib import Path
import importlib

import uvicorn

import src.config as config
import src.database as database

RUNTIME_DIR = Path(__file__).resolve().parent / 'tmp_e2e_runtime'
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

config.DATA_DIR = RUNTIME_DIR
config.DATABASE_PATH = RUNTIME_DIR / 'irms.db'
database.DATA_DIR = config.DATA_DIR
database.DATABASE_PATH = config.DATABASE_PATH

import src.main as main
main = importlib.reload(main)

if __name__ == '__main__':
    uvicorn.run(main.app, host='127.0.0.1', port=8765, log_level='warning')
