# 평가용 API 서버 이미지
# 빌드: docker build -t disclosure-agent .
# 실행: docker run -d --env-file .env -p 80:80 --name disclosure-agent disclosure-agent
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV TOOLS_BACKEND=real \
    PYTHONUNBUFFERED=1

EXPOSE 80
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "80"]
