# PostgreSQL V1 로컬 실행

PostgreSQL 15 개발 환경은 Docker Compose로 고정한다. pgvector는 이 단계에서
설치하지 않는다.

```powershell
Copy-Item .env.example .env
# .env의 POSTGRES_PASSWORD와 DATABASE_URL 비밀번호를 같은 값으로 변경
docker compose up -d
docker compose ps
```

스키마와 데이터 적재·검증:

```powershell
python -m pip install "psycopg[binary]>=3.2,<4"
python src/db/load_postgres.py --database-url $env:DATABASE_URL
python src/db/validate_postgres.py --database-url $env:DATABASE_URL --output reports/postgres_validation.json
python -m unittest tests.test_postgres_ingestion -v
```

`.env`는 `.gitignore` 대상이다. 저장소에는 `.env.example`만 둔다.

종료와 재시작:

```powershell
docker compose stop
docker compose start
```

볼륨까지 삭제하는 `docker compose down -v`는 적재 DB를 삭제하므로 일반 종료에는
사용하지 않는다.
