@echo off
chcp 65001 > nul
echo =========================================================================
echo    HỆ THỐNG ĐIỀU PHỐI VÀ GHÉP CHUYẾN GỌI XE - HÀ NỘI (GRAB SIMULATOR)
echo =========================================================================
echo.
echo  → Đang kiểm tra môi trường Python...
python --version
echo.
echo  → Khởi động FastAPI WebSocket Server...
echo  → Mở trình duyệt tại: http://localhost:8000
echo  → Nhấn Ctrl+C để dừng server.
echo =========================================================================
echo.
set PYTHONIOENCODING=utf-8
start "" http://localhost:8000
python server.py
pause
