WITH best AS (
 SELECT s.scenario_id, s.location, s.plant, s.draw, s.tonnes_per_yr,
 count(*) FILTER (WHERE c.n_washes BETWEEN 0 AND 1) AS simple_candidates,
 count(*) FILTER (WHERE c.n_washes BETWEEN 2 AND 3) AS extra_candidates,
 max(c.profit_usd_per_yr) FILTER (WHERE c.n_washes BETWEEN 0 AND 1) AS simple_profit_usd_per_yr,
 max(c.profit_usd_per_yr) FILTER (WHERE c.n_washes BETWEEN 2 AND 3) AS extra_profit_usd_per_yr
 FROM mc_candidates c JOIN mc_scenarios s USING(scenario_id,location)
 WHERE s.feed='published' AND s.location='A' AND s.energy_case='C1' AND s.draw IS NOT NULL
 AND s.plant IN ('dedicated','shared20k') AND c.downstream_key <> 'resale'
 GROUP BY s.scenario_id,s.location,s.plant,s.draw,s.tonnes_per_yr)
 SELECT *,extra_profit_usd_per_yr-simple_profit_usd_per_yr AS advantage_usd_per_yr,
 (extra_profit_usd_per_yr-simple_profit_usd_per_yr)/tonnes_per_yr AS advantage_usd_per_t_feed
 FROM best ORDER BY plant,draw;
