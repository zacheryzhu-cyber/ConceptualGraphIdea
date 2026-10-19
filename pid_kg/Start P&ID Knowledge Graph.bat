@echo off
cd /d "%~dp0"
rem Neo4j Aura connection (URI / user / password) comes from neo4j_demo.env, so no login is needed
if exist neo4j_demo.env (
  for /f "usebackq eol=# tokens=1,* delims==" %%a in ("neo4j_demo.env") do set "%%a=%%b"
)
call .venv\Scripts\activate.bat
streamlit run app.py
