# Populeaza baza cu date sintetice ca sa verificam ca report.py chiar ruleaza
import numpy as np, pandas as pd
from sqlalchemy.orm import Session
from src.db import get_engine, init_db, upsert_observations
from src.transform import normalize

rng = np.random.default_rng(42)
# 15 minute: aceeasi rezolutie pe care o livreaza ENTSO-E pentru RO
idx = pd.date_range('2026-06-01', periods=4*24*30, freq='15min', tz='UTC')
# consum cu tipar zilnic + saptamanal
base = 6000 + 1500*np.sin((idx.hour-6)/24*2*np.pi) - 600*(idx.dayofweek>=5)
load = pd.DataFrame({'timestamp': idx, 'value': base + rng.normal(0,200,len(idx)), 'quantity_unit': 'MAW'})
price = pd.DataFrame({'timestamp': idx, 'value': 80 + 40*np.sin((idx.hour-8)/24*2*np.pi) + rng.normal(0,10,len(idx)),
                      'currency': 'EUR', 'price_unit': 'MWH'})

engine = get_engine(); init_db(engine)
with Session(engine) as s:
    upsert_observations(s, normalize(load, country='RO', metric='load_actual', unit='MW'))
    upsert_observations(s, normalize(price, country='RO', metric='price_day_ahead', unit='EUR/MWh'))
print("date sintetice incarcate")
