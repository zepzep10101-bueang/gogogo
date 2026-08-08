FROM python:3.10-slim

WORKDIR /app

COPY . .

# 누나가 미리 만들어둔 재료 목록(requirements)으로 설치하기
RUN pip install -r requirements.txt

EXPOSE 8080

# 누나의 파이썬 파일 이름(main_new)으로 실행!
CMD ["python", "-m", "uvicorn", "main_new:app", "--host", "0.0.0.0", "--port", "8080"]
