"""Append absent database-role passwords to local .env without printing them."""
from pathlib import Path
import secrets


def main():
    path=Path(__file__).resolve().parents[1]/'.env'
    if not path.exists():raise SystemExit('Create .env from .env.example first')
    content=path.read_text(encoding='utf-8-sig')
    keys={line.split('=',1)[0].strip() for line in content.splitlines() if '=' in line and not line.lstrip().startswith('#')}
    missing=[k for k in ('RF_MIGRATOR_PASSWORD','RF_APP_PASSWORD','RF_READONLY_PASSWORD') if k not in keys]
    if missing:
        with path.open('a',encoding='utf-8',newline='\n') as stream:
            stream.write('\n# Restricted database credentials (local only)\n')
            for key in missing:stream.write(key+'='+secrets.token_hex(24)+'\n')
    print('Database role credentials ready; existing values preserved.')


if __name__=='__main__':main()
