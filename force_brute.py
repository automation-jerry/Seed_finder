#!/usr/bin/env python3
"""
GitHub Actions Random Seed Brute Forcer
Checks random seed phrases for balances across multiple chains
"""

import os
import sys
import json
import asyncio
import aiohttp
import secrets
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

try:
    from mnemonic import Mnemonic
    from hdwallet import BIP44HDWallet
    from hdwallet.cryptocurrencies import (
        BitcoinMainnet, EthereumMainnet, LitecoinMainnet, 
        DogecoinMainnet, BitcoinCashMainnet
    )
except ImportError as e:
    print(f"Error importing: {e}")
    print("Run: pip install -r requirements.txt")
    sys.exit(1)


@dataclass
class WalletResult:
    seed_index: int
    seed_phrase: str
    chain: str
    address: str
    balance: float
    balance_usd: float = 0.0
    has_funds: bool = False


class Config:
    def __init__(self, config_file: str = "config.json"):
        self.config_file = config_file
        self.load()
    
    def load(self):
        defaults = {
            "seeds_to_check": 100,
            "chains": ["BTC", "ETH", "BSC", "LTC", "DOGE"],
            "save_all_results": False,
            "stop_on_fund": True,
            "delay_between_checks": 0.2,
            "concurrent_checks": 10
        }
        
        if Path(self.config_file).exists():
            with open(self.config_file, 'r') as f:
                loaded = json.load(f)
                defaults.update(loaded)
        
        self.__dict__.update(defaults)
    
    def save(self):
        with open(self.config_file, 'w') as f:
            json.dump(self.__dict__, f, indent=2)


class RandomWalletScanner:
    def __init__(self, config: Config):
        self.config = config
        self.session: Optional[aiohttp.ClientSession] = None
        self.price_cache: Dict[str, float] = {}
        self.mnemo = Mnemonic("english")
        self.checked_count = 0
        self.found_count = 0
        
        # Chain configurations
        self.CHAINS = {
            'BTC': {'symbol': 'BTC', 'crypto': BitcoinMainnet, 'path': "m/44'/0'/0'/0/0"},
            'ETH': {'symbol': 'ETH', 'crypto': EthereumMainnet, 'path': "m/44'/60'/0'/0/0"},
            'BSC': {'symbol': 'BNB', 'crypto': EthereumMainnet, 'path': "m/44'/60'/0'/0/0"},
            'MATIC': {'symbol': 'MATIC', 'crypto': EthereumMainnet, 'path': "m/44'/60'/0'/0/0"},
            'LTC': {'symbol': 'LTC', 'crypto': LitecoinMainnet, 'path': "m/44'/2'/0'/0/0"},
            'DOGE': {'symbol': 'DOGE', 'crypto': DogecoinMainnet, 'path': "m/44'/3'/0'/0/0"},
            'BCH': {'symbol': 'BCH', 'crypto': BitcoinCashMainnet, 'path': "m/44'/145'/0'/0/0"},
        }
        
        self.RPCS = {
            'ETH': ['https://ethereum-rpc.publicnode.com', 'https://rpc.ankr.com/eth'],
            'BSC': ['https://bsc-rpc.publicnode.com', 'https://rpc.ankr.com/bsc'],
            'MATIC': ['https://polygon-rpc.publicnode.com', 'https://rpc.ankr.com/polygon'],
        }

    def generate_random_seed(self) -> str:
        """Generate a random 12-word seed phrase"""
        entropy = secrets.token_bytes(16)
        return self.mnemo.to_mnemonic(entropy)

    async def init_session(self):
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        self.session = aiohttp.ClientSession(timeout=timeout)
        await self._load_prices()

    async def close_session(self):
        if self.session:
            await self.session.close()

    async def _load_prices(self):
        """Load crypto prices for USD calculation"""
        try:
            url = "https://api.coingecko.com/api/v3/simple/price"
            coins = 'bitcoin,ethereum,binancecoin,matic-network,dogecoin,litecoin,bitcoin-cash'
            async with self.session.get(f"{url}?ids={coins}&vs_currencies=usd", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.price_cache = {
                        'BTC': data.get('bitcoin', {}).get('usd', 0),
                        'ETH': data.get('ethereum', {}).get('usd', 0),
                        'BNB': data.get('binancecoin', {}).get('usd', 0),
                        'MATIC': data.get('matic-network', {}).get('usd', 0),
                        'DOGE': data.get('dogecoin', {}).get('usd', 0),
                        'LTC': data.get('litecoin', {}).get('usd', 0),
                        'BCH': data.get('bitcoin-cash', {}).get('usd', 0),
                    }
                    print(f"💰 Loaded prices: BTC=${self.price_cache.get('BTC', 0)}")
        except Exception as e:
            print(f"⚠️ Could not load prices: {e}")

    def derive_address(self, seed: str, chain: str) -> Optional[str]:
        """Derive address from seed (synchronous)"""
        try:
            config = self.CHAINS[chain]
            wallet = BIP44HDWallet(cryptocurrency=config['crypto']())
            wallet.from_mnemonic(mnemonic=seed)
            wallet.clean_derivation()
            wallet.from_path(config['path'])
            return wallet.address()
        except Exception as e:
            return None

    async def check_balance(self, address: str, chain: str) -> float:
        """Check balance for an address"""
        try:
            if chain == 'BTC':
                url = f"https://api.blockcypher.com/v1/btc/main/addrs/{address}/balance"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('balance', 0) / 1e8
                        
            elif chain == 'LTC':
                url = f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}/balance"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('balance', 0) / 1e8
                        
            elif chain == 'DOGE':
                url = f"https://api.blockcypher.com/v1/doge/main/addrs/{address}/balance"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get('balance', 0) / 1e8
                        
            elif chain == 'BCH':
                url = f"https://api.blockchair.com/bitcoin-cash/dashboards/address/{address}"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data['data'].get(address, {}).get('address', {}).get('balance', 0) / 1e8
                        
            elif chain in self.RPCS:
                rpc = self.RPCS[chain][0]
                payload = {
                    "jsonrpc": "2.0",
                    "method": "eth_getBalance",
                    "params": [address, "latest"],
                    "id": 1
                }
                async with self.session.post(rpc, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return int(data.get('result', '0x0'), 16) / 1e8
                        
            return 0.0
        except Exception as e:
            return 0.0

    async def check_seed(self, seed: str, index: int) -> List[WalletResult]:
        """Check one seed across all configured chains"""
        results = []
        
        for chain in self.config.chains:
            # Derive address
            address = self.derive_address(seed, chain)
            if not address:
                continue
            
            # Check balance
            balance = await self.check_balance(address, chain)
            
            symbol = self.CHAINS.get(chain, {}).get('symbol', chain)
            price = self.price_cache.get(symbol, 0)
            
            result = WalletResult(
                seed_index=index,
                seed_phrase=seed if balance > 0 else "",  # Only store if funded
                chain=chain,
                address=address,
                balance=balance,
                balance_usd=balance * price,
                has_funds=balance > 0
            )
            results.append(result)
            
            if balance > 0:
                self.found_count += 1
                print(f"\n🎉 FOUND FUNDS!")
                print(f"   Seed: {seed}")
                print(f"   Chain: {chain}")
                print(f"   Address: {address}")
                print(f"   Balance: {balance} {symbol} (${balance * price:.2f})")
        
        return results

    async def run(self) -> dict:
        """Main brute force loop"""
        print("=" * 70)
        print("🎰 RANDOM SEED BRUTE FORCER")
        print("=" * 70)
        print(f"📊 Configuration:")
        print(f"   Seeds to check: {self.config.seeds_to_check}")
        print(f"   Chains: {', '.join(self.config.chains)}")
        print(f"   Stop on fund: {self.config.stop_on_fund}")
        print("=" * 70)
        print("\n⚠️  PROBABILITY WARNING:")
        print("   Finding a funded random seed is astronomically unlikely!")
        print("   This is for educational/demonstration purposes only.")
        print("=" * 70 + "\n")
        
        await self.init_session()
        
        start_time = time.time()
        all_results = []
        funded_seeds = []
        
        try:
            for i in range(1, self.config.seeds_to_check + 1):
                self.checked_count = i
                
                # Generate and check seed
                seed = self.generate_random_seed()
                results = await self.check_seed(seed, i)
                all_results.extend(results)
                
                # Check if any have funds
                funded = [r for r in results if r.has_funds]
                if funded:
                    funded_seeds.append({
                        'seed': seed,
                        'timestamp': datetime.now().isoformat(),
                        'balances': [asdict(r) for r in funded]
                    })
                    
                    if self.config.stop_on_fund:
                        print(f"\n⛔ Stopping because funded wallet found!")
                        break
                
                # Progress report
                if i % 10 == 0 or i == 1:
                    elapsed = time.time() - start_time
                    speed = i / elapsed if elapsed > 0 else 0
                    print(f"⏳ Progress: {i}/{self.config.seeds_to_check} | "
                          f"Found: {self.found_count} | "
                          f"Speed: {speed:.1f} seeds/sec")
                
                # Delay to be nice to APIs
                if self.config.delay_between_checks > 0:
                    await asyncio.sleep(self.config.delay_between_checks)
        
        finally:
            await self.close_session()
        
        elapsed = time.time() - start_time
        
        # Compile results
        final_results = {
            'scan_time': datetime.now().isoformat(),
            'seeds_checked': self.checked_count,
            'funded_found': self.found_count,
            'elapsed_seconds': round(elapsed, 2),
            'seeds_per_second': round(self.checked_count / elapsed, 2) if elapsed > 0 else 0,
            'funded_seeds': funded_seeds,
            'all_results': [asdict(r) for r in all_results] if self.config.save_all_results else []
        }
        
        # Print summary
        print("\n" + "=" * 70)
        print("📊 FINAL RESULTS")
        print("=" * 70)
        print(f"Seeds checked: {final_results['seeds_checked']}")
        print(f"Time elapsed: {final_results['elapsed_seconds']:.1f}s")
        print(f"Speed: {final_results['seeds_per_second']:.1f} seeds/sec")
        print(f"Funded wallets found: {final_results['funded_found']}")
        
        if funded_seeds:
            print("\n🎉 JACKPOT WALLETS:")
            for jackpot in funded_seeds:
                print(f"\n   Seed: {jackpot['seed']}")
                for bal in jackpot['balances']:
                    print(f"   {bal['chain']}: {bal['balance']} (${bal['balance_usd']:.2f})")
        else:
            print("\n❌ No funded wallets found (as expected)")
        
        print("=" * 70)
        
        return final_results


def save_results(results: dict, filename: str = "results.json"):
    """Save results to JSON file"""
    with open(filename, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n💾 Results saved to: {filename}")


def save_jackpot_seed(seed: str, filename: str = "JACKPOT_SEED.txt"):
    """Save funded seed to separate file"""
    with open(filename, 'w') as f:
        f.write(f"JACKPOT SEED FOUND!\n")
        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
        f.write(f"Seed: {seed}\n")
        f.write(f"\nIMPORT THIS SEED INTO A WALLET IMMEDIATELY!\n")
    print(f"🎰 JACKPOT saved to: {filename}")


async def main():
    # Load config
    config = Config()
    
    # Override from environment variables
    if 'SEEDS_TO_CHECK' in os.environ:
        config.seeds_to_check = int(os.environ['SEEDS_TO_CHECK'])
    if 'CHAINS' in os.environ:
        config.chains = os.environ['CHAINS'].split(',')
    
    # Run scanner
    scanner = RandomWalletScanner(config)
    results = await scanner.run()
    
    # Save results
    save_results(results, "results.json")
    
    # Save jackpot separately if found
    if results['funded_seeds']:
        for jackpot in results['funded_seeds']:
            save_jackpot_seed(jackpot['seed'])
        
        # Also save to GitHub Actions output
        if 'GITHUB_OUTPUT' in os.environ:
            with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
                f.write(f"jackpot_found=true\n")
                f.write(f"seed={results['funded_seeds'][0]['seed']}\n")
    
    # Save to GitHub Actions summary
    if 'GITHUB_STEP_SUMMARY' in os.environ:
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write("## 🎰 Brute Force Results\n\n")
            f.write(f"- Seeds checked: {results['seeds_checked']}\n")
            f.write(f"- Time: {results['elapsed_seconds']}s\n")
            f.write(f"- Funded found: {results['funded_found']}\n")
            if results['funded_seeds']:
                f.write("\n### 🎉 JACKPOT!\n")
                for jackpot in results['funded_seeds']:
                    f.write(f"```\n{jackpot['seed']}\n```\n")
    
    return results


if __name__ == '__main__':
    results = asyncio.run(main())
    
    # Exit with error code if jackpot found (to trigger notifications)
    if results['funded_found'] > 0:
        print("\n🎉 JACKPOT FOUND! Exiting with special code.")
        sys.exit(42)  # Special exit code for jackpot
    else:
        sys.exit(0)