import math
from datetime import date
from scipy.stats import norm

def black_scholes_price(S, K, T, r, sigma, is_call):
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    if is_call:
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

def implied_volatility(S, K, T, r, market_price, is_call, tolerance=1e-5, max_iterations=100):
    sigma = 0.3  # initial guess
    for _ in range(max_iterations):
        price = black_scholes_price(S, K, T, r, sigma, is_call)
        vega = S * norm.pdf((math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))) * math.sqrt(T)
        diff = price - market_price

        if abs(diff) < tolerance:
            return sigma

        if vega == 0:
            break

        sigma -= diff / vega
        if sigma <= 0:
            sigma = 1e-4
    return max(sigma, 1e-4)  # prevent zero or negative IV

def calculate_greeks(spot_price, strike_price, expiry_date, option_price, is_call, interest_rate=0.06):
    days_to_expiry = max((expiry_date - date.today()).days, 1)
    T = days_to_expiry / 365.0
    S = spot_price
    K = strike_price
    r = interest_rate

    iv = implied_volatility(S, K, T, r, option_price, is_call)
    d1 = (math.log(S / K) + (r + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
    d2 = d1 - iv * math.sqrt(T)

    delta = norm.cdf(d1) if is_call else -norm.cdf(-d1)
    theta = (
        -S * norm.pdf(d1) * iv / (2 * math.sqrt(T))
        - r * K * math.exp(-r * T) * (norm.cdf(d2) if is_call else -norm.cdf(-d2))
    ) / 365
    gamma = norm.pdf(d1) / (S * iv * math.sqrt(T))
    vega = S * norm.pdf(d1) * math.sqrt(T) / 100

    return {
        'iv': iv * 100,
        'delta': delta,
        'theta': theta,
        'gamma': gamma,
        'vega': vega
    }
