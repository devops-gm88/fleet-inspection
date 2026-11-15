# The demo is this one command. `make verify` proves the acceptance criteria.
demo:
	./demo.sh

verify:
	python3 -m fleet verify

test:
	python3 -m unittest discover -s tests -v

stats:
	python3 -m fleet stats

clean:
	rm -rf out

.PHONY: demo verify test stats clean
