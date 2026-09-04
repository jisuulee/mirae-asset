# 평가 서버 배포 가이드 (4번 인계용)

서버 코드(server.py)는 완성 상태. 4번의 몫 = 배포 + 무중단 운영 (9/7~9/20).

## 로컬 기동 확인 (누구나 1분)

```powershell
pip install fastapi "uvicorn[standard]"
uvicorn server:app --host 0.0.0.0 --port 8000
# 다른 터미널에서:
curl "http://localhost:8000/health"
curl "http://localhost:8000/answer?question_id=T-1&question=삼성전자 2025년 매출액"
```

전제: 같은 폴더 .env 에 CLOVASTUDIO_API_KEY + POSTGRES_* 5줄,
PostgreSQL 컨테이너 기동 + 적재 완료 상태.

## 배포 구성 (NCP 권장, ngrok 백업)

### A. NCP (권장)
1. VPC + Public Subnet + 서버 (High-CPU 2vCPU/4GB, Ubuntu, 공인 IP 1개)
   — 설명회 권장 스펙 그대로. 크레딧 잔여 확인.
2. ACG(방화벽): 80, 443 인바운드 허용 (+ 관리용 22)
3. 서버에 설치: Docker(권장) 또는 Python 3.12 + PostgreSQL
   - DB 는 서버 안에서 docker compose 로 동일하게 재현
     (data/ 원본 + role2_final + analysis_results 업로드 필요 — 약 6GB)
   - 또는 데스크탑 DB 를 pg_dump 로 떠서 서버에서 restore (원본 업로드 불필요, 권장)
     데스크탑: docker exec <컨테이너> pg_dump -U disclosure disclosure > dump.sql
     서버:     psql -U disclosure disclosure < dump.sql
4. 앱 기동: uvicorn server:app --host 0.0.0.0 --port 80
   (443 필요 시 nginx 리버스 프록시 + certbot, 필수는 아님 — 공지상 80/443 중 택1)
5. 자동 재시작: systemd 유닛 (Restart=always) — 아래 템플릿

```ini
# /etc/systemd/system/disclosure.service
[Unit]
After=network.target docker.service
[Service]
WorkingDirectory=/home/ubuntu/mirae-asset
ExecStart=/usr/bin/python3 -m uvicorn server:app --host 0.0.0.0 --port 80
Restart=always
RestartSec=5
EnvironmentFile=/home/ubuntu/mirae-asset/.env
[Install]
WantedBy=multi-user.target
```

6. 외부 검증: 팀원 아무나 외부망(폰 LTE)에서
   http://<공인IP>/health 열리는지 + /answer 1건 확인
7. 원격 하네스: 데스크탑에서
   python eval/harness.py --api http://<공인IP> --limit 3

### B. ngrok (백업 — 공지상 허용)
데스크탑을 서버로 쓰는 경우: ngrok http 8000 → 발급 URL 을 제출.
단점: 데스크탑 2주 무중단 + 무료 플랜 URL 변동 리스크 → 유료 고정 도메인 권장.
A 실패 시에만.

## 운영 체크리스트 (9/6 프리즈 전 완료)
- [ ] /health 외부 접속 OK
- [ ] /answer 실질의 3종 (정상/확인불가/인젝션) 응답 확인
- [ ] systemd 재시작 테스트 (프로세스 kill 후 자동 복구)
- [ ] 서버 재부팅 테스트 (DB 컨테이너 + 앱 자동 기동)
- [ ] logs/requests.jsonl 기록 확인
- [ ] 제출물(README)에 End-point URL + API 명세 기재
- [ ] 9/6 이후 서버·코드 변경 금지 (실격 조항) — 감시만
