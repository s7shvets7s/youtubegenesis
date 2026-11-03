@echo off
setlocal EnableExtensions DisableDelayedExpansion

title RU to EN dubbing pipeline
pushd "%~dp0.."

set "PIPELINE_PYTHON=%CD%\.venv\Scripts\python.exe"
set "WHISPERX_PYTHON=%CD%\.venv-whisperx\Scripts\python.exe"
set "PYTHONPATH=%CD%\src"

echo.
echo ================================================
echo   Full YouTube translation and dubbing pipeline
echo ================================================
echo.

if not exist "%PIPELINE_PYTHON%" (
    echo ERROR: the .venv environment was not found.
    echo Expected file: %PIPELINE_PYTHON%
    goto :failed
)

set "VIDEO_URL="
set /p "VIDEO_URL=Paste the YouTube link and press Enter: "

if not defined VIDEO_URL (
    echo.
    echo ERROR: no link was entered.
    goto :failed
)

echo.
echo Choose the video mode:
echo   1 - Clean source: keep useful footage, always replace the author
echo   2 - Hybrid: also replace maps, charts, processes, and abstract explanations
echo   3 - Rebuild: replace the complete picture
echo   4 - Original: preserve every source frame; only remove confirmed ads
set "VIDEO_MODE_CHOICE=2"
set /p "VIDEO_MODE_CHOICE=Enter 1, 2, 3 or 4 [2]: "

echo.
set "USE_AVATAR=N"
set /p "USE_AVATAR=Add the local MuseTalk avatar? Y/N [N]: "
if /I "%USE_AVATAR%"=="Y" (
    set "AVATAR_SOURCE="
    set /p "AVATAR_SOURCE=Full path to the front-facing avatar motion video: "
    if not defined AVATAR_SOURCE (
        echo ERROR: avatar video path was not entered.
        goto :failed
    )
)

if not exist "%WHISPERX_PYTHON%" (
    echo ERROR: the .venv-whisperx environment was not found.
    echo Expected file: %WHISPERX_PYTHON%
    goto :failed
)

"%WHISPERX_PYTHON%" -c "import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)" >nul 2>&1
if errorlevel 1 (
    echo ERROR: WhisperX CUDA is unavailable. The pipeline will not fall back to slow CPU transcription.
    echo Reinstall .venv-whisperx using requirements/whisperx.txt.
    goto :failed
)

echo.
echo Pipeline started. You can leave this window in the background.
echo The result will be saved in the jobs folder.
echo.

if /I "%USE_AVATAR%"=="Y" (
    "%PIPELINE_PYTHON%" -m ru_en_dubber.cli --source-url-env VIDEO_URL --video-mode-env VIDEO_MODE_CHOICE --jobs-dir "%CD%\jobs" --diarize --avatar-mode musetalk --avatar-source-env AVATAR_SOURCE
) else (
    "%PIPELINE_PYTHON%" -m ru_en_dubber.cli --source-url-env VIDEO_URL --video-mode-env VIDEO_MODE_CHOICE --jobs-dir "%CD%\jobs" --diarize
)
set "PIPELINE_EXIT_CODE=%ERRORLEVEL%"

echo.
if "%PIPELINE_EXIT_CODE%"=="0" (
    echo ================================================
    echo   Done. The Premiere package is in the jobs folder.
    echo ================================================
) else (
    echo ================================================
    echo   Pipeline failed with error code %PIPELINE_EXIT_CODE%.
    echo   See the error message above for details.
    echo ================================================
)

echo.
pause
popd
exit /b %PIPELINE_EXIT_CODE%

:failed
echo.
pause
popd
exit /b 1
