# 1. 무거운 옷 벗고 가벼운 slim 버전 입기 (용량 대폭 감소)
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 2. 레일웨이용 포트 설정 적용 완료
CMD sh -c "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"
