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

# productie pe surse: solar ziua, eolian variabil, hidro si gaz acopera restul
hour = idx.hour + idx.minute / 60
solar = np.clip(2800*np.sin((hour-6)/13*np.pi), 0, None)
wind = np.clip(700 + 500*np.sin(np.arange(len(idx))/300) + rng.normal(0,80,len(idx)), 0, None)
sources = {
    'Solar': solar, 'Wind Onshore': wind, 'Nuclear': np.full(len(idx), 1300.0),
    'Hydro Water Reservoir': np.clip(900 - 0.25*solar, 50, None),
    'Fossil Gas': np.clip(base - solar - wind - 1300, 300, None),
}
# pretul scade cand solarul e mare si urca la varful de seara, ca pe piata reala
price = pd.DataFrame({'timestamp': idx, 'value': 150 - 0.04*solar + 40*np.exp(-((hour-20)**2)/4) + rng.normal(0,10,len(idx)),
                      'currency': 'EUR', 'price_unit': 'MWH'})
gen = pd.concat([pd.DataFrame({'timestamp': idx, 'psr_type': psr, 'value': v, 'quantity_unit': 'MAW'})
                 for psr, v in sources.items()], ignore_index=True)

engine = get_engine(); init_db(engine)
with Session(engine) as s:
    upsert_observations(s, normalize(load, country='RO', metric='load_actual', unit='MW'))
    upsert_observations(s, normalize(price, country='RO', metric='price_day_ahead', unit='EUR/MWh'))
    upsert_observations(s, normalize(gen, country='RO', metric='generation_actual', unit='MW'))
print("date sintetice incarcate")
