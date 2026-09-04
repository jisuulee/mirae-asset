미래에셋 공모전 인프라·배포 작업 정리
작성일: 2026년 9월 5일
오늘 한 작업
NCP에 평가용 서버를 구축했다.
서버 구성
NCP Ubuntu 24.04 VM
2 vCPU, 메모리 8GB, 스토리지 60GB
공인 IP: 49.50.143.130
SSH 22번, API 80번 포트 사용
서버에 Docker와 Docker Compose를 설치했다.
FastAPI와 PostgreSQL은 별도 클라우드 서비스가 아니라 같은 VM에서 Docker 컨테이너로 실행하도록 구성했다.
Kubernetes, Argo CD, 로드밸런서, 오토스케일링, 별도 이미지 레지스트리는 사용하지 않았다. 현재 규모에서는 단일 VM 구성이 더 간단하고 비용도 적게 들기 때문이다.
GitHub Actions 자동 배포도 구성했다.
main 브랜치에 코드가 올라오면 GitHub Actions가 NCP VM으로 소스를 전송하고, 서버에서 Docker 이미지를 빌드한 뒤 FastAPI와 PostgreSQL을 실행한다.
배포 실패 시 이전 컨테이너로 복구할 수 있도록 구성했다.
배포 중 발생한 문제
Docker 빌드 과정에서 requirements.txt를 찾지 못하는 오류가 발생했다.
.dockerignore의 *.txt 설정 때문에 requirements.txt도 제외된 것이 원인이었다. !requirements.txt 예외 설정을 추가해서 해결했다.
GitHub Secret 이름이 잘못돼 서버 주소가 전달되지 않는 문제도 있었다.
Secret을 다음 세 개로 나눠 등록해서 해결했다.
NCP_HOST
NCP_USERNAME
NCP_PASSWORD
현재 결과
GitHub Actions 자동 배포 성공
성공한 실행:
Deploy to NCP #4
외부에서 다음 API에 접속되는 것까지 확인했다.
http://49.50.143.130/health
http://49.50.143.130/answer
서버, Docker, FastAPI, PostgreSQL 연결은 정상이다.
다만 PostgreSQL에 아직 테이블과 공시 데이터가 들어오지 않아 /health 상태는 degraded로 표시된다. 따라서 현재는 “배포 완료, 데이터 연결 대기” 상태다.
남은 작업
1. 역할 2 담당자에게 PostgreSQL dump 파일 받기
2. VM의 PostgreSQL 컨테이너에 DB 복원하기
3. 서버 .env에 DB 정보와 HyperCLOVA X API 키 설정하기
4. /health가 정상 상태인지 확인하기
5. /answer에서 실제 공시 질문과 출처가 정상적으로 반환되는지 확인하기
6. 자체 평가 스크립트와 요청 로그 확인하기
7. README에 최종 API 주소와 실행 방법 작성하기
발표용 요약
NCP 단일 VM에 FastAPI와 PostgreSQL을 Docker로 구성했고, GitHub Actions를 이용해 main 브랜치의 코드를 자동 배포하도록 구축했습니다. 외부 API 접속과 자동 배포까지 검증했으며, 현재는 PostgreSQL 데이터 적재와 HyperCLOVA X 연결만 남아 있습니다.
