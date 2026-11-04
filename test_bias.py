from src.core.directional_bias import DirectionalBiasCalculator

calc = DirectionalBiasCalculator()
result = calc.compute_bias(
    range_low=28610.80,
    range_high=28834.00,
    breaker_low_front=28673.00,
    breaker_high_front=28759.00,
    last_breaker_dir="BULLISH",
    elapsed_seconds=1080,
    mitigation_touches=0,
    timeframe="15m",
    prior_regime="STRONG_BULLISH"
)

print("\n--- DIRECTIONAL BIAS ENGINE BENCHMARK ---")
for k, v in result.items():
    print(f"  {k:<15}: {v}")
print("----------------------------------------\n")
