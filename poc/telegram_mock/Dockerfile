FROM python:3.12-slim

WORKDIR /app
COPY telegram_mock/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY telegram_mock/mock_bot.py .
# BC-012: shared health-check package — every container COPYs this.
COPY beaconry_health/ ./beaconry_health/

EXPOSE 8080
CMD ["python3", "-u", "-m", "uvicorn", "mock_bot:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
