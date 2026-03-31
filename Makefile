.PHONY: dev build test clean
dev:
	nix develop
build:
	nix build 'path:.' 2>/dev/null || echo "no default package"
test:
	echo "no tests configured"
clean:
	rm -f result
