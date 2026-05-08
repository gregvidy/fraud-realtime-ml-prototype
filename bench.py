"""Quick async benchmark to measure real server-side latency."""
import asyncio, time, statistics, random, uuid
import httpx

async def bench(n_requests=1000, concurrency=100):
    payload_template = {
        'currency': 'USD', 'payment_method': 'card',
        'country_code': 'US', 'is_international': False, 'local_hour': 10,
    }
    sem = asyncio.Semaphore(concurrency)
    latencies = []

    async def fire(client):
        p = dict(payload_template)
        p['transaction_id'] = str(uuid.uuid4())
        p['user_id'] = f'u_{random.randint(1,2000):06d}'
        p['device_id'] = f'd_{random.randint(1,4000):07d}'
        p['merchant_id'] = f'm_{random.randint(1,300):05d}'
        p['amount'] = round(random.uniform(5, 2000), 2)
        async with sem:
            t0 = time.perf_counter()
            r = await client.post('http://localhost:8000/score', json=p)
            lat = (time.perf_counter() - t0) * 1000
            latencies.append(lat)

    async with httpx.AsyncClient() as client:
        t_start = time.perf_counter()
        tasks = [fire(client) for _ in range(n_requests)]
        await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - t_start

    latencies.sort()
    n = len(latencies)
    print(f'Requests: {n}, Concurrency: {concurrency}')
    print(f'Elapsed: {elapsed:.2f}s, RPS: {n/elapsed:.1f}')
    print(f'p50: {latencies[n//2]:.1f}ms, p95: {latencies[int(n*0.95)]:.1f}ms, p99: {latencies[int(n*0.99)]:.1f}ms')
    print(f'Min: {latencies[0]:.1f}ms, Max: {latencies[-1]:.1f}ms, Avg: {statistics.mean(latencies):.1f}ms')

if __name__ == '__main__':
    asyncio.run(bench())
