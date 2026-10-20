# Space kiểu Docker (khuyến nghị): chỉ chạy gateway MCP, KHÔNG render video.
FROM python:3.12-slim
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user app.py .
COPY --chown=user app ./app
EXPOSE 7860
CMD ["python", "app.py"]
