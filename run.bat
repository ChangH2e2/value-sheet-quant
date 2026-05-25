@echo off
chcp 65001 > nul
echo ========================================
echo   Value Sheet 퀀트 대시보드 시작
echo ========================================

:: 패키지 설치 여부 확인
python -c "import flask" 2>nul
if errorlevel 1 (
    echo 필요한 패키지를 설치합니다...
    pip install -r requirements.txt
)

echo.
echo 서버 시작 중... http://localhost:5000 으로 접속하세요
echo (처음 실행 시 데이터 수집에 5-10분 소요됩니다)
echo.

:: 브라우저 자동 실행 (3초 후)
start /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:5000"

python server.py
pause
