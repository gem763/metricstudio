from pathlib import Path
import pandas as pd

p = Path('static') / 'code_name.pkl'
code_name = pd.read_pickle(p)

mask = code_name.astype(str).str.contains('스팩')
print('total', len(code_name), 'spac', int(mask.sum()))
print(code_name[mask].head(30))
