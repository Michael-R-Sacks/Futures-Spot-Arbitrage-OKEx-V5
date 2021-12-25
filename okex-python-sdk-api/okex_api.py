import okex.account as account
import okex.public as public
import okex.trade as trade
from okex.exceptions import OkexException, OkexAPIException
import key
import time
from log import fprint
import lang
import asyncio
from asyncio import create_task, gather

SLEEP = 0.2
CONFIRMATION = 3


def round_to(number, fraction):
    """返回fraction的倍数
    """
    # 小数点后位数
    ndigits = len('{:f}'.format(fraction - int(fraction))) - 2
    if ndigits > 0:
        return round(int(number / fraction) * fraction, ndigits)
    else:
        return round(int(number / fraction) * fraction)


class OKExAPI:
    """基本OKEx功能类
    """

    def __init__(self, coin: str = None, accountid=3):
        # print('OKExAPI init started')
        self.accountid = accountid
        apikey = key.Key(accountid)
        api_key = apikey.api_key
        secret_key = apikey.secret_key
        passphrase = apikey.passphrase

        if accountid == 3:
            self.accountAPI = account.AccountAPI(api_key, secret_key, passphrase, test=True)
            self.tradeAPI = trade.TradeAPI(api_key, secret_key, passphrase, test=True)
            self.publicAPI = public.PublicAPI(test=True)
        else:
            self.accountAPI = account.AccountAPI(api_key, secret_key, passphrase, False)
            self.tradeAPI = trade.TradeAPI(api_key, secret_key, passphrase, False)
            self.publicAPI = public.PublicAPI()

        self.coin = coin
        if coin:
            self.spot_ID = coin + '-USDT'
            self.swap_ID = coin + '-USDT-SWAP'

            self.exitFlag = False
            self.exist = True

            # 公共-获取现货信息
            # 公共-获取合约信息
            try:
                # begin = time.monotonic()
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 在async上下文内呼叫构造函数，不能再run
                    # print('Event loop is running. Call with await.')
                    pass
                else:
                    # 在async上下文外呼叫构造函数
                    self.spot_info = loop.create_task(
                        self.publicAPI.get_specific_instrument('SPOT', self.spot_ID))
                    self.swap_info = loop.create_task(
                        self.publicAPI.get_specific_instrument('SWAP', self.swap_ID))
                    loop.run_until_complete(gather(self.spot_info, self.swap_info))
                    self.spot_info = self.spot_info.result()
                    self.swap_info = self.swap_info.result()
                # print(f'OKExAPI({self.coin}) init finished')
                # end = time.monotonic()
                # print(f'OKExAPI init takes {end-begin} s')
            except Exception as e:
                print(f'OKExAPI({self.coin}) init error')
                print(e)
                self.exist = False
                fprint(lang.nonexistent_crypto.format(self.coin))
        else:
            self.exist = False

    def __await__(self):
        """异步构造函数\n
        await OKExAPI()先召唤__init__()，然后是awaitable __await__()。

        :return:OKExAPI
        """
        if self.coin:
            try:
                # print('OKExAPI__await__ started')
                # begin = time.monotonic()
                self.spot_info = create_task(self.publicAPI.get_specific_instrument('SPOT', self.spot_ID))
                self.swap_info = create_task(self.publicAPI.get_specific_instrument('SWAP', self.swap_ID))
                yield from gather(self.spot_info, self.swap_info)
                self.spot_info = self.spot_info.result()
                self.swap_info = self.swap_info.result()
                # print('OKExAPI__await__ finished')
                # end = time.monotonic()
                # print('OKExAPI__await__ takes {:f} s'.format(end-begin))
            except Exception as e:
                print(f'OKExAPI__await__({self.coin}) error')
                print(e)
                self.exist = False
                fprint(lang.nonexistent_crypto.format(self.coin))
        else:
            self.exist = False
        return self

    async def check_account_level(self):
        """检查账户模式，需开通合约交易
        """
        level = (await self.accountAPI.get_account_config())['acctLv']
        if level == '1':
            fprint(lang.upgrade_account)
            exit()

    async def check_position_mode(self):
        """检查是否为买卖模式
        """
        mode = (await self.accountAPI.get_account_config())['posMode']
        if mode != 'net_mode':
            fprint(lang.position_mode.format(mode))
            await self.accountAPI.set_position_mode('net_mode')
            fprint(lang.change_net_mode)
        mode = (await self.accountAPI.get_account_config())['posMode']
        if mode != 'net_mode':
            fprint(lang.set_mode_fail)
            exit()

    async def usdt_balance(self):
        """获取USDT保证金
        """
        return await self.spot_position('USDT')

    async def spot_position(self, coin=None):
        """获取现货余额
        """
        if not coin:
            coin = self.coin
        data: list = (await self.accountAPI.get_coin_account(coin))['details']
        if data:
            return float(data[0]['availEq'])
        else:
            return 0.

    async def swap_holding(self, swap_ID=None):
        """获取合约持仓
        """
        if not swap_ID:
            swap_ID = self.swap_ID
        result: list = await self.accountAPI.get_specific_position(swap_ID)
        for n in result:
            if n['mgnMode'] == 'isolated':
                return n
        return None

    async def swap_position(self, swap_ID=None):
        """获取合约仓位
        """
        if not swap_ID:
            swap_ID = self.swap_ID
        swap_info = await self.publicAPI.get_specific_instrument('SWAP', swap_ID)
        contract_val = float(swap_info['ctVal'])
        holding = await self.swap_holding(swap_ID)
        if holding and holding['pos']:
            return - float(holding['pos']) * contract_val
        else:
            return 0.

    async def swap_balance(self):
        """获取占用保证金
        """
        holding = await self.swap_holding(self.swap_ID)
        if holding and holding['margin']:
            return float(holding['margin'])
        else:
            return 0.

    async def get_lever(self):
        setting = await self.accountAPI.get_leverage(self.swap_ID, 'isolated')
        return int(float(setting['lever']))

    async def add_margin(self, transfer_amount):
        """增加保证金

        :param transfer_amount: 划转金额
        :return: 是否成功
        :rtype: bool
        """
        if transfer_amount <= 0:
            return False
        try:
            if await self.accountAPI.adjust_margin(instId=self.swap_ID, posSide='net', type='add', amt=transfer_amount):
                fprint(lang.added_margin, transfer_amount, "USDT")
                return True
            else:
                return False
        except OkexAPIException as e:
            fprint(e)
            fprint(lang.transfer_failed)
            if e.code == "58110":
                time.sleep(600)
            return False

    async def reduce_margin(self, transfer_amount):
        """减少保证金

        :param transfer_amount: 划转金额
        :return: 是否成功
        :rtype: bool
        """
        if transfer_amount <= 0:
            return False
        try:
            if await self.accountAPI.adjust_margin(instId=self.swap_ID, posSide='net', type='reduce',
                                                   amt=transfer_amount):
                fprint(lang.reduced_margin, transfer_amount, "USDT")
                return True
            else:
                return False
        except OkexAPIException as e:
            fprint(e)
            fprint(lang.transfer_failed)
            if e.code == "58110":
                time.sleep(600)
            return False

    async def get_tickers(self):
        return await gather(self.publicAPI.get_specific_ticker(self.spot_ID),
                            self.publicAPI.get_specific_ticker(self.swap_ID))