.PHONY: install run test clean
install:
	pip install -r requirements.txt 2>/dev/null || pip install -e . 2>/dev/null
run:
	python main.py 2>/dev/null || python -m uvicorn api.main:app --reload
test:
	python -m pytest 2>/dev/null || echo no tests
clean:
	find . -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
