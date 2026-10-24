"""Small-model coding benchmark: ask a model for Python functions, run its answers against tests.

    python experiments/code_bench/bench.py easy gemma12-test
    python experiments/code_bench/bench.py hard gemma12-test other-model

Thinking is OFF and each answer may use up to 2000 tokens. An answer passes only if its code runs and
every assert in the task's test holds. Generated code is run with `python -I` in a temporary folder with a
10 s limit, after a check that it imports nothing dangerous and doesn't call eval/exec/open.
Results and the story behind the first numbers are in README.md."""
import ast
import os
import subprocess
import sys
import tempfile
import time

EASY = [
 ("dedupe", "Write a Python function dedupe(xs) that removes duplicates from a list but keeps the first occurrence order.",
  "assert dedupe([3,1,3,2,1])==[3,1,2]\nassert dedupe([])==[]\nassert dedupe(['a','a'])==['a']"),
 ("is_pal", "Write a Python function is_pal(s) that returns True if s is a palindrome ignoring case and non-alphanumeric characters.",
  "assert is_pal('A man, a plan, a canal: Panama')\nassert not is_pal('hello')\nassert is_pal('')"),
 ("parse_kv", "Write a Python function parse_kv(s) turning 'a=1;b=22' into {'a':1,'b':22} (values are ints; ignore empty parts).",
  "assert parse_kv('a=1;b=22')=={'a':1,'b':22}\nassert parse_kv('')=={}\nassert parse_kv('x=5;')=={'x':5}"),
 ("merge", "Write a Python function merge(intervals) that merges overlapping [start,end] intervals and returns them sorted as a list of lists.",
  "assert merge([[1,3],[2,6],[8,10]])==[[1,6],[8,10]]\nassert merge([[1,4],[4,5]])==[[1,5]]\nassert merge([])==[]\nassert merge([[5,6],[1,2]])==[[1,2],[5,6]]"),
 ("fizz", "Write a Python function fizz(n) returning a list of strings for 1..n: 'Fizz' for multiples of 3, 'Buzz' for 5, 'FizzBuzz' for both, else the number as a string.",
  "assert fizz(15)[-1]=='FizzBuzz'\nassert fizz(5)==['1','2','Fizz','4','Buzz']\nassert fizz(0)==[]"),
 ("flatten", "Write a Python function flatten(x) that flattens arbitrarily nested lists into one flat list.",
  "assert flatten([1,[2,[3,[4]]],5])==[1,2,3,4,5]\nassert flatten([])==[]\nassert flatten([[],[[]]])==[]"),
 ("top_words", "Write a Python function top_words(text, n) returning the n most common lowercase words (split on non-letters) as a list of (word,count), ties broken alphabetically.",
  "assert top_words('b a b a c',2)==[('a',2),('b',2)]\nassert top_words('Hi hi, HI there',1)==[('hi',3)]"),
 ("roman", "Write a Python function roman_to_int(s) converting a Roman numeral string to an integer.",
  "assert roman_to_int('MCMXCIV')==1994\nassert roman_to_int('IX')==9\nassert roman_to_int('III')==3"),
 ("fix_bug", "This function should return the sum of the last k items of a list, but it is wrong. Fix it and return the full corrected function named last_sum.\n\ndef last_sum(xs, k):\n    return sum(xs[-k+1:])",
  "assert last_sum([1,2,3,4],2)==7\nassert last_sum([1,2,3],3)==6\nassert last_sum([5],1)==5"),
 ("bsearch", "Write a Python function bsearch(xs, t) doing binary search on a sorted list, returning the index of t or -1.",
  "assert bsearch([1,3,5,7],5)==2\nassert bsearch([1,3,5,7],4)==-1\nassert bsearch([],1)==-1\nassert bsearch([2],2)==0"),
]
HARD = [
 ("lru", "Write a Python class LRU with __init__(self, capacity), get(key) (returns -1 if missing) and put(key, value), evicting the least recently used key when over capacity. Both get and put count as use.",
  "c=LRU(2); c.put(1,1); c.put(2,2); assert c.get(1)==1; c.put(3,3); assert c.get(2)==-1; c.put(4,4); assert c.get(1)==-1 and c.get(3)==3 and c.get(4)==4\nc=LRU(1); c.put(5,5); c.put(5,6); assert c.get(5)==6"),
 ("evalexpr", "Write a Python function calc(s) that evaluates an arithmetic expression string with + - * / , parentheses, unary minus and spaces, using correct precedence. Do not use eval. Return a float.",
  "assert calc('2+3*4')==14\nassert calc('(2+3)*4')==20\nassert calc('-3 + 5')==2\nassert calc('10/4')==2.5\nassert calc('2*(3+(4-1))*2')==24\nassert calc(' 7 ')==7"),
 ("toposort", "Write a Python function topo(deps) where deps is a dict mapping a task to a list of tasks it depends on. Return a list of all tasks (including ones only named as dependencies) in an order where every task comes after its dependencies; break ties alphabetically. Raise ValueError on a cycle.",
  "assert topo({'a':['b'],'b':['c'],'c':[]})==['c','b','a']\nassert topo({'x':[],'a':[]})==['a','x']\nassert topo({'a':['z']})==['z','a']\ntry:\n    topo({'a':['b'],'b':['a']}); assert False\nexcept ValueError: pass"),
 ("wordwrap", "Write a Python function wrap(text, width) that wraps text to lines of at most width characters, breaking only at spaces, never splitting a word unless a single word is longer than width (then put it alone on its own line). Return a list of lines with no trailing spaces.",
  "assert wrap('the quick brown fox',10)==['the quick','brown fox']\nassert wrap('a bb ccc',3)==['a','bb','ccc']\nassert wrap('abcdefghij kl',4)==['abcdefghij','kl']\nassert wrap('',5)==[]"),
 ("csvparse", "Write a Python function parse_csv_line(line) that splits one CSV line into fields, handling double-quoted fields that may contain commas and escaped quotes written as \"\". Return a list of strings.",
  "assert parse_csv_line('a,b,c')==['a','b','c']\nassert parse_csv_line('a,\"b,c\",d')==['a','b,c','d']\nassert parse_csv_line('\"he said \"\"hi\"\"\",x')==['he said \"hi\"','x']\nassert parse_csv_line('a,,b')==['a','','b']\nassert parse_csv_line('')==['']"),
 ("fix_median", "This function should return the median of a list of numbers (average the two middle values for even length), but it has bugs. Fix it and return the full corrected function named median.\n\ndef median(xs):\n    xs.sort()\n    n = len(xs)\n    if n % 2 == 0:\n        return xs[n//2]\n    return xs[n//2 + 1]",
  "assert median([3,1,2])==2\nassert median([4,1,3,2])==2.5\nassert median([5])==5\norig=[3,1,2]; median(orig); assert orig==[3,1,2]"),
 ("rle", "Write two Python functions: rle_encode(s) turning 'aaabcc' into '3a1b2c', and rle_decode(s) reversing it (counts may have multiple digits, e.g. '12x').",
  "assert rle_encode('aaabcc')=='3a1b2c'\nassert rle_encode('')==''\nassert rle_decode('3a1b2c')=='aaabcc'\nassert rle_decode(rle_encode('x'*12+'y'))=='x'*12+'y'"),
 ("bracket", "Write a Python function balanced(s) that returns True if all brackets ()[]{} in the string are correctly nested and matched, ignoring other characters.",
  "assert balanced('([]{})')\nassert not balanced('(]')\nassert not balanced('((')\nassert balanced('a(b)c')\nassert not balanced(')(')\nassert balanced('')"),
 ("islands", "Write a Python function count_islands(grid) where grid is a list of strings of '0' and '1'; return the number of 4-directionally connected groups of '1'. Do not use recursion deeper than needed for large grids (use an explicit stack or queue).",
  "assert count_islands(['110','010','001'])==2\nassert count_islands([])==0\nassert count_islands(['0'])==0\nassert count_islands(['1'*50]*50)==1\nassert count_islands(['101','010','101'])==5"),
 ("bank", "Write a Python class Account with deposit(x), withdraw(x) and balance property. withdraw must raise ValueError('insufficient funds') if x exceeds the balance; deposit and withdraw must raise ValueError for amounts <= 0. Keep a history list of ('deposit'|'withdraw', amount) for successful operations only.",
  "a=Account(); a.deposit(50); a.withdraw(20); assert a.balance==30\ntry:\n    a.withdraw(100); assert False\nexcept ValueError as e: assert str(e)=='insufficient funds'\ntry:\n    a.deposit(0); assert False\nexcept ValueError: pass\nassert a.history==[('deposit',50),('withdraw',20)]"),
]
SYS = "You are a careful Python programmer. Reply with one complete python code block and nothing else."
FORBIDDEN_IMPORTS = {"os", "sys", "subprocess", "socket", "shutil"}
FORBIDDEN_CALLS = {"eval", "exec", "open", "__import__"}


def code_of(text: str) -> str:
    import re
    m = re.findall(r"```(?:python)?\n(.*?)```", text, re.S)
    return (m[0] if m else text).strip()


def unsafe(code: str):
    """Why this code must not be run, or None. Looks at the syntax tree, so a word in a docstring
    or comment (e.g. 'without using eval()') is not mistaken for a call."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None                       # not runnable anyway; it will simply fail
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] in FORBIDDEN_IMPORTS for a in node.names):
            return "forbidden import"
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in FORBIDDEN_IMPORTS:
            return "forbidden import"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALLS:
            return f"calls {node.func.id}"
    return None


def check(code: str, test: str) -> str:
    why = unsafe(code)
    if why:
        return f"blocked ({why})"
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.py")
        with open(path, "w") as f:
            f.write(code + "\n\n" + test + "\n")
        try:
            r = subprocess.run([sys.executable, "-I", path], capture_output=True, text=True, timeout=10, cwd=d)
        except subprocess.TimeoutExpired:
            return "timeout"
        return "pass" if r.returncode == 0 else "fail: " + (r.stderr.strip().splitlines() or [""])[-1][:70]


def run(model: str, tasks) -> int:
    import ollama
    score = 0
    for name, prompt, test in tasks:
        t = time.time()
        try:
            r = ollama.chat(model=model, messages=[{"role": "system", "content": SYS}, {"role": "user", "content": prompt}],
                            think=False, keep_alive="2m", options={"num_ctx": 4096, "temperature": 0.1, "num_predict": 2000})
            out = check(code_of(r["message"]["content"]), test)
        except Exception as e:
            out = f"error: {str(e)[:50]}"
        score += out == "pass"
        print(model, name, out, round(time.time() - t, 1), flush=True)
    print(model, "SCORE", score, "/", len(tasks), flush=True)
    try:
        ollama.chat(model=model, messages=[], keep_alive=0)
    except Exception:
        pass
    return score


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in ("easy", "hard"):
        sys.exit("usage: bench.py easy|hard <model> [<model> ...]")
    for m in sys.argv[2:]:
        run(m, EASY if sys.argv[1] == "easy" else HARD)
