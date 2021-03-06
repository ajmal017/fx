#####################################################
import datetime
from ib_insync import *
from ibapi import *
import logging
import pytz
import sys
import pandas as pd
import pandas_ta as ta

#####################################################
# Algorithmic strategy class for interactive brokers:
class IBAlgoStrategy(object):
    """
    Algorithmic trading strategy for Interactive Brokers
    """

    def __init__(self):
        """Initialize Algorithm"""
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.handler = logging.FileHandler('IBKRTradingAlgorithm.log')
        self.handler.setLevel(logging.INFO)
        self.formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        self.handler.setFormatter(self.formatter)
        self.logger.addHandler(self.handler)
        self.logger.info('Starting log at {}'.format(datetime.datetime.now()))

        # Connect to IB
        self.ib = self.connect()

        # Create empty list of instruments
        self.instruments = []

        # Run main loop
        self.run()

#####################################################
    def run(self):
        """Run logic for today's trading"""
        self.log()
        start_time = datetime.datetime.now(tz=pytz.timezone('Asia/Shanghai'))
        self.log('Beginning to run trading algorithm at {} HKT'
                 .format(start_time))

        for instrument in self.instruments:

            # INITIAL VARIABLE SETUP
            # Indicators
            indicators = self.get_indicators(instrument)
            # Cash balance for current instrument as units of that instrument
            cash_balance = self.get_cash_balance(instrument)
            # Is the total unit (max 4 entries) full?
            unit_full = True
            # Are we long/short on this instrument?
            is_long = False
            is_short = False

            # Maximum unit size (2% of portfolio) in base currency
            max_unit_size = 0
            for v in self.ib.accountSummary():
                if v.currency == 'BASE' and v.tag == 'CashBalance':
                    max_unit_size = float(v.value) * float(0.02)

            # Check if long or short based on whether >/< 100 units are traded
            if cash_balance > float(100):
                is_long = True

            if cash_balance < float(-100):
                is_short = True

            # Check if current unit is small enough to be not full
            if abs(self.get_cash_balance(instrument)
               * self.get_atr_multiple(instrument,
                                       indicators,
                                       multiplier=0.5)
               * self.get_base_exchange(instrument)) < \
               max_unit_size:
                if is_long or is_short:
                    unit_full = False

            # If not long or short, place initial entry orders:
            if not is_long and not is_short:
                orders = self.get_open_trades(instrument)
                for o in orders:
                    self.ib.cancelOrder(o.order)
                self.place_initial_entry_orders(instrument, indicators)

            # If there is a unit that is not full:
            if not unit_full:

                # Save stop information:
                stops = []
                for t in self.get_open_trades(instrument):
                    if "sl" in t.order.orderRef:
                        stops.append(self.place_order(instrument,
                                     self.ib.client.getReqId(),
                                     action=t.order.action,
                                     order_type=t.order.orderType,
                                     tif=t.order.tif,
                                     total_quantity=t.order.totalQuantity,
                                     transmit=t.order.transmit,
                                     price_condition=t.order.conditions[0].price,
                                     order_ref=instrument.localSymbol + "_sl_" +
                                     str(t.order.orderRef)[-1:],
                                     is_more=t.order.conditions[0].isMore))
                
                self.log("Saved stops {}".format(stops))

                # Cancel open (unfilled) orders:
                for t in self.get_open_trades(instrument):
                    self.ib.cancelOrder(t.order)

                # Check how many more entries can be made before unit is full.
                # i=4 indicates the unit is full.
                if abs(self.get_cash_balance(instrument)
                       * self.get_base_exchange(instrument)
                       * self.get_atr_multiple(instrument,
                                               indicators,
                                               multiplier=0.5)) < \
                   max_unit_size * float(0.25):
                    i = 1
                elif abs(self.get_cash_balance(instrument)
                         * self.get_base_exchange(instrument)
                         * self.get_atr_multiple(instrument,
                                                 indicators,
                                                 multiplier=0.5)) < \
                        max_unit_size * float(0.5):
                    i = 2
                elif abs(self.get_cash_balance(instrument)
                         * self.get_base_exchange(instrument)
                         * self.get_atr_multiple(instrument,
                                                 indicators,
                                                 multiplier=0.5)) < \
                        max_unit_size * float(0.75):
                    i = 3
                else:
                    i = 4

                # Set compound order offset to be last fill price:
                last_fill_price = 0
                for f in self.get_filled_executions(instrument):
                    if f.execution.avgPrice > last_fill_price:
                        last_fill_price = f.execution.avgPrice

                # If long (>100 units), place compound long and exit orders:
                if is_long:

                    # Create compound orders
                    while i < 4:
                        for o in self.go_long(instrument,
                                              indicators,
                                              offset=i,
                                              is_compound_order=True,
                                              last_fill_price=last_fill_price):
                            self.ib.placeOrder(instrument, o)
                            self.ib.sleep(1)
                        i += 1

                    # Create exit order
                    long_exit_all = self.go_long(instrument,
                                                 indicators,
                                                 total_quantity=cash_balance,
                                                 is_exit_all=True)

                    # Put all stops and exit orders into an OCA:
                    oca = []
                    for s in stops:
                        oca.append(s)
                    for o in long_exit_all:
                        oca.append(o)
                    self.ib.oneCancelsAll(orders=oca,
                                          ocaGroup="OCA_"
                                          + str(instrument.localSymbol)
                                          + str(self.ib.client.getReqId()),
                                          ocaType=2)

                    # Place all orders:
                    for o in oca:
                        self.ib.placeOrder(instrument, o)
                        self.ib.sleep(1)

                # If short (<100 units), place compound short and exit orders:
                elif is_short:
                    # Create compound orders
                    while i < 4:
                        for o in self.go_short(instrument,
                                               indicators,
                                               offset=i,
                                               is_compound_order=True,
                                               last_fill_price=last_fill_price):
                            self.ib.placeOrder(instrument, o)
                            self.ib.sleep(1)
                        i += 1

                    # Create exit order
                    short_exit_all = self.go_short(instrument,
                                                   indicators,
                                                   total_quantity=cash_balance,
                                                   is_exit_all=True)

                    # Put all stops and exit orders into an OCA:
                    oca = []
                    for s in stops:
                        oca.append(s)
                    for o in short_exit_all:
                        oca.append(o)
                    self.ib.oneCancelsAll(orders=oca,
                                          ocaGroup="OCA_"
                                          + str(instrument.localSymbol)
                                          + str(self.ib.client.getReqId()),
                                          ocaType=2)

                    # Place all orders:
                    for o in oca:
                        self.ib.placeOrder(instrument, o)
                        self.ib.sleep(1)

            # VARIABLES USED IN LOGGING ONLY
            # Current total unit size in base currency.
            current_unit = round(cash_balance
                                 * self.get_atr_multiple(instrument,
                                                         indicators,
                                                         multiplier=0.5)
                                 * self.get_base_exchange(instrument))
            self.log('Currently risking {} base currency on {}'
                     .format(current_unit, instrument.localSymbol))

####################################################
    def connect(self):
        """Connect to Interactive Brokers TWS"""

        self.log('Connecting to Interactive Brokers TWS...')
        try:
            ib = IB()
            ib.connect('127.0.0.1', 7497, clientId=0)
            ib.reqAutoOpenOrders(True)
            # Requesting manual pending orders doesn't work with this:
            # ib.connect('127.0.0.1', 7497, clientId=1)
            self.log('Connected')
            self.log()
            return ib
        except:
            self.log('Error in connecting to TWS!! Exiting...')
            self.log(sys.exc_info()[0])
            exit(-1)

#####################################################
    def log(self, msg=""):
        """Add log to output file"""
        self.logger.info(msg)
        print(msg)

#####################################################
    def get_open_trades(self, instrument):
        """Returns the number of unfilled trades open for a currency"""
        orders = []
        self.ib.sleep(1)
        for t in self.ib.openTrades():
            if t.contract.localSymbol == instrument.localSymbol:
                orders.append(t)
        order_count = len(orders)
        self.log('Currently in {} open orders for instrument {}.'
                 .format(order_count, instrument.localSymbol))
        return orders

#####################################################
    def get_filled_executions(self, instrument):
        """Returns the number of filled executions in past week"""
        fills = []
        self.ib.sleep(1)
        for f in self.ib.reqExecutions():
            if f.contract.localSymbol == instrument.localSymbol:
                self.log('Found trade with symbol {}: {}'.format(f.contract.localSymbol, f.execution.avgPrice))
                fills.append(f)
        fill_count = len(fills)
        self.log('Currently in {} filled trades for instrument {}.'
                 .format(fill_count, instrument.localSymbol))
        return fills

#####################################################
    def add_instrument(self, instrument_type, ticker,
                       symbol, currency, exchange='IDEALPRO'):
        """Adds instrument as an IB contract to instruments list"""
        self.log("Adding instrument {}".format(ticker))

        if instrument_type == 'Forex':
            instrument = Forex(ticker, exchange=exchange,
                               symbol=symbol, currency=currency)
        else:
            raise ValueError(
                       "Invalid instrument type: {}".format(instrument_type))

        self.ib.qualifyContracts(instrument)
        self.instruments.append(instrument)

#####################################################
    def get_available_funds(self):
        """Returns available funds in USD"""
        account_values = self.ib.accountValues()
        available_funds = 0
        i = 0
        for value in account_values:
            if account_values[i].tag == 'AvailableFunds':
                available_funds = float(account_values[i].value)
                break
            i += 1
        self.log('Available funds: {} {}'.format(available_funds,
                                                 account_values[i].currency))
        return available_funds

#####################################################
    def get_cash_balance(self, instrument):
        """Returns current position for currency pair in units"""
        account_values = self.ib.accountValues()
        cash_balance = 0
        i = 0
        for value in account_values:
            if account_values[i].tag == 'CashBalance' and \
               account_values[i].currency == instrument.localSymbol[0:3]:
                cash_balance = float(account_values[i].value)
                break
            i += 1
        self.log("Current {} cash balance: {} units"
                 .format(instrument.localSymbol[0:3], cash_balance))
        return cash_balance

#####################################################
    def get_base_exchange(self, instrument):
        """Get the exchange rate between currency and base"""
        assert (instrument.localSymbol in ['GBP.JPY', 'AUD.CAD', 'EUR.USD']), \
               'Invalid Currency!'

        base = ""

        for v in self.ib.accountValues():
            if v.tag == 'AvailableFunds':
                base = v.currency

        if base == instrument.localSymbol[-3:]:
            return 1
        elif instrument.localSymbol[-3:] != 'USD':
            pair = base + instrument.localSymbol[-3:]
            self.log("Getting current exchange rate for pair {}".format(pair))

            ticker = self.ib.reqMktData(contract=Forex(pair=pair,
                                                       symbol=base,
                                                       currency=instrument
                                                       .localSymbol[-3:]))
            self.ib.sleep(1)
            return 1 / ticker.marketPrice()
        elif instrument.localSymbol[-3:] == 'USD':
            pair = instrument.localSymbol[-3:] + base
            self.log("Getting current exchange rate for pair {}".format(pair))

            ticker = self.ib.reqMktData(contract=Forex(pair=pair,
                                                       symbol=instrument
                                                       .localSymbol[-3:],
                                                       currency=base))
            self.ib.sleep(1)
            return ticker.marketPrice()

#####################################################
    def set_position_size(self, instrument, indicators, sl_size):
        """Sets position size in BASE based on available funds and volitility"""
        position_size = 0

        # Get position size in BASE
        available_funds = self.get_available_funds()
        equity_at_risk = available_funds * 0.005

        base = ""

        for v in self.ib.accountValues():
            if v.tag == 'AvailableFunds':
                base = v.currency

        if base == instrument.localSymbol[-3:]:
            position_size = round(equity_at_risk / sl_size)
        else:
            position_size = round((1 / self.get_base_exchange(instrument))
                                  * equity_at_risk
                                  / sl_size)

        return position_size

#####################################################
    def get_atr_multiple(self, instrument, indicators, multiplier=0.5):
        """Sets absolute value of SL equal to 1/2 ATR"""
        indicators = self.get_indicators(instrument)
        volatility = indicators['atr'][(indicators.axes[0].stop - 1)]
        sl_size = self.adjust_for_price_increments(instrument,
                                                   multiplier * volatility)
        # self.log('Current ATR={}, sl={}'.format(volatility, sl_size))
        return sl_size

#####################################################
    def adjust_for_price_increments(self, instrument, value):
        """Adjust given value for instrument's allowed price increments."""
        increment = None
        if instrument.localSymbol == 'EUR.USD':
            increment = 0.00005
        elif instrument.localSymbol == 'GBP.JPY':
            increment = 0.005
        elif instrument.localSymbol == 'AUD.CAD':
            increment = 0.00005
        else:
            self.log('Invalid pair! Cannot calculate SL!')
            return None
        value = increment * round(value / increment)
        return value

#####################################################
    def go_short(self, instrument, indicators, *args, **kwargs):
        """Place short order according to strategy with an offset from LTL"""
        offset = kwargs.get('offset', 0)
        last_fill_price = kwargs.get('last_fill_price', None)
        is_exit_all = kwargs.get('is_exit_all', False)
        is_compound_order = kwargs.get('is_compound_order', False)
        sl_size = kwargs.get('sl_size',
                             self.get_atr_multiple(instrument, indicators))
        total_quantity = kwargs.get('total_quantity',
                                    self.set_position_size(instrument,
                                                           indicators,
                                                           sl_size))
        long_term_low = self.adjust_for_price_increments(instrument,
                                                         indicators
                                                         ['long_dcl']
                                                         [(indicators.axes[0]
                                                           .stop - 1)])
        short_exit_condition = self.adjust_for_price_increments(instrument,
                                                                indicators
                                                                ['short_dcu']
                                                                [(indicators
                                                                 .axes[0]
                                                                 .stop - 1)]) \
            - offset * self.get_atr_multiple(instrument, indicators)

        orders = []

        if is_exit_all:
            orders.append(self.place_order(instrument,
                          self.ib.client.getReqId(),
                          action="BUY",
                          order_type="MKT",
                          tif="GTC",
                          total_quantity=total_quantity,
                          transmit=True,
                          price_condition=short_exit_condition,
                          is_more=True,
                          order_ref=str(instrument.localSymbol +
                                        "_short_exit_all")))
            return orders

        compound_order_ref = ""
        price_condition = long_term_low

        if is_compound_order and last_fill_price:
            compound_order_ref = "_compound"
            price_condition = last_fill_price \
                - offset * self.get_atr_multiple(instrument, indicators)

        short_entry = self.place_order(instrument=instrument,
                                       order_id=self.ib.client.getReqId(),
                                       action="SELL",
                                       order_type="MKT",
                                       total_quantity=total_quantity,
                                       transmit=False,
                                       price_condition=price_condition,
                                       is_more=False,
                                       order_ref=str(instrument.localSymbol
                                                     + compound_order_ref
                                                     + "_short_entry"))

        short_sl = self.place_order(instrument=instrument,
                                    order_id=self.ib.client.getReqId(),
                                    action="BUY",
                                    order_type="MKT",
                                    total_quantity=total_quantity,
                                    transmit=False,
                                    parent_id=short_entry.orderId,
                                    price_condition=price_condition + sl_size,
                                    is_more=True,
                                    order_ref=str(instrument.localSymbol
                                                  + compound_order_ref
                                                  + "_short_sl"))
        short_exit = self.place_order(instrument=instrument,
                                      order_id=self.ib.client.getReqId(),
                                      action="BUY",
                                      order_type="MKT",
                                      total_quantity=total_quantity,
                                      transmit=True,
                                      parent_id=short_entry.orderId,
                                      price_condition=short_exit_condition,
                                      is_more=True,
                                      order_ref=str(instrument.localSymbol
                                                    + compound_order_ref
                                                    + "_short_exit"))

        orders = [short_entry,
                  short_sl,
                  short_exit]
        return orders

#####################################################
    def go_long(self, instrument, indicators, *args, **kwargs):
        """Return long order according to strategy with an offset from LTH"""
        offset = kwargs.get('offset', 0)
        last_fill_price = kwargs.get('last_fill_price', None)
        sl_size = kwargs.get('sl_size',
                             self.get_atr_multiple(instrument, indicators))
        is_exit_all = kwargs.get('is_exit_all', False)
        is_compound_order = kwargs.get('is_compound_order', False)
        total_quantity = kwargs.get('total_quantity',
                                    self.set_position_size(instrument,
                                                           indicators,
                                                           sl_size))
        long_term_high = self.adjust_for_price_increments(instrument,
                                                          indicators
                                                          ['long_dcu']
                                                          [(indicators.axes[0]
                                                            .stop - 1)])
        long_exit_condition = self.adjust_for_price_increments(instrument,
                                                               indicators
                                                               ['short_dcl']
                                                               [(indicators
                                                                .axes[0]
                                                                .stop - 1)]) \
            + offset * self.get_atr_multiple(instrument, indicators)

        orders = []

        if is_exit_all:
            self.log("Placing exit order for complete unit! Parameters:")
            self.log("Total quantity = {}, \
                     price condition = {}, order ref = {}"
                     .format(total_quantity,
                             long_exit_condition,
                             str(instrument.localSymbol +
                                 "_long_exit_all")))
            orders.append(self.place_order(instrument,
                          self.ib.client.getReqId(),
                          action="SELL",
                          order_type="MKT",
                          tif="GTC",
                          total_quantity=total_quantity,
                          transmit=True,
                          is_more=False,
                          price_condition=long_exit_condition,
                          order_ref=str(instrument.localSymbol +
                                        "_long_exit_all")))
            return orders

        compound_order_ref = ""
        price_condition = long_term_high

        if is_compound_order and last_fill_price:
            compound_order_ref = "_compound"
            price_condition = last_fill_price \
                + offset * self.get_atr_multiple(instrument, indicators)

        long_entry = self.place_order(instrument=instrument,
                                      order_id=self.ib.client.getReqId(),
                                      action="BUY",
                                      order_type="MKT",
                                      total_quantity=total_quantity,
                                      transmit=False,
                                      price_condition=price_condition,
                                      is_more=True,
                                      order_ref=str(instrument.localSymbol
                                                    + compound_order_ref
                                                    + "_long_entry"))
        long_sl = self.place_order(instrument=instrument,
                                   order_id=self.ib.client.getReqId(),
                                   action="SELL",
                                   order_type="MKT",
                                   total_quantity=total_quantity,
                                   transmit=False,
                                   parent_id=long_entry.orderId,
                                   price_condition=price_condition - sl_size,
                                   is_more=False,
                                   order_ref=str(instrument.localSymbol
                                                 + compound_order_ref
                                                 + "_long_sl"))
        long_exit = self.place_order(instrument=instrument,
                                     order_id=self.ib.client.getReqId(),
                                     action="SELL",
                                     order_type="MKT",
                                     total_quantity=total_quantity,
                                     transmit=True,
                                     parent_id=long_entry.orderId,
                                     price_condition=long_exit_condition,
                                     is_more=False,
                                     order_ref=str(instrument.localSymbol
                                                   + compound_order_ref
                                                   + "_long_exit"))

        orders = [long_entry,
                  long_sl,
                  long_exit]
        return orders

#####################################################
    def place_initial_entry_orders(self, instrument, indicators):
        """Places initial long & short order entries with IBKR for instrument"""
        # Trade parameters:
        sl_size = self.get_atr_multiple(instrument, indicators)
        total_quantity = self.set_position_size(instrument,
                                                indicators,
                                                sl_size)

        # Create initial short order entries:
        long_entry_attempts = self.go_long(instrument,
                                           indicators,
                                           sl_size=sl_size,
                                           total_quantity=total_quantity)

        # Create initial short order entries:
        short_entry_attempts = self.go_short(instrument,
                                             indicators,
                                             sl_size=sl_size,
                                             total_quantity=total_quantity)

        # Put long and short order entries into OCA:
        self.ib.oneCancelsAll(orders=[long_entry_attempts[0],
                                      short_entry_attempts[0]],
                              ocaGroup="OCA_"
                              + str(instrument.localSymbol)
                              + str(self.ib.client.getReqId()),
                              ocaType=1)

        # Place orders:
        orders = []
        for o in long_entry_attempts:
            orders.append(o)
        for o in short_entry_attempts:
            orders.append(o)

        for o in orders:
            self.ib.placeOrder(instrument, o)
            self.ib.sleep(1)

#####################################################
    def place_order(self,
                    instrument,
                    order_id,
                    action,
                    order_type,
                    tif="GTC",
                    total_quantity=0,
                    transmit=False,
                    *args, **kwargs):
        """Places order with IBKR given relevant info.
        kwargs:
        bool is_more - True if price condition is >, False if <
        bool price_condition - True if there is a price condition, else False
        order_ref - can manually input order reference number
        parent_id - can manually input parent order ID"""
        is_more = kwargs.get('is_more', "ERROR")
        price_condition = kwargs.get('price_condition', "ERROR")
        parent_id = kwargs.get('parent_id', "ERROR")
        order_ref = kwargs.get('order_ref', "ERROR")

        order = Order()
        order.orderId = order_id
        order.action = action
        order.orderType = order_type
        order.totalQuantity = total_quantity
        order.transmit = transmit
        order.tif = tif
        if parent_id != "ERROR":
            order.parentId = parent_id

        if order_ref != "ERROR":
            order.orderRef = order_ref

        if price_condition != "ERROR" and is_more != "ERROR":
            order.conditions = [PriceCondition(conId = instrument.conId,
                                               exch='IDEALPRO',
                                               isMore=is_more,
                                               price=price_condition)]

        return order

#####################################################
    def get_indicators(self, instrument):
        """Returns 55 & 20 donchian channels for instrument"""
        bars = self.ib.reqHistoricalData(contract=instrument,
                                         endDateTime='',
                                         durationStr='6 M',
                                         barSizeSetting='1 day',
                                         whatToShow='MIDPOINT',
                                         useRTH=True)
        df = pd.DataFrame(bars)
        del df['volume']
        del df['barCount']
        del df['average']
        atr = pd.DataFrame(ta.atr(high=df['high'],
                           low=df['low'],
                           close=df['close'],
                           length=20))
        long_donchian = pd.DataFrame(ta.donchian(high=df['high'],
                                                 low=df['low'],
                                                 upper_length=55,
                                                 lower_length=55))
        short_donchian = pd.DataFrame(ta.donchian(high=df['high'],
                                                  low=df['low'],
                                                  upper_length=20,
                                                  lower_length=20))
        df = pd.concat([df, atr, long_donchian, short_donchian],
                       axis=1,
                       join="outer")
        df.columns.values[5] = 'atr'
        df.columns.values[6] = 'long_dcl'
        df.columns.values[7] = 'long_dcm'
        df.columns.values[8] = 'long_dcu'
        df.columns.values[9] = 'short_dcl'
        df.columns.values[10] = 'short_dcm'
        df.columns.values[11] = 'short_dcu'
        # self.log(df.tail())
        return df

#####################################################
# MAIN PROGRAMME:
if __name__ == '__main__':
    # Create algo object
    algo = IBAlgoStrategy()

    # Add instruments to trade
    algo.add_instrument('Forex', ticker='GBPJPY', symbol='GBP', currency='JPY')
    algo.add_instrument('Forex', ticker='EURUSD', symbol='EUR', currency='USD')
    # algo.add_instrument('Forex', ticker='AUDCAD', symbol='AUD', currency='CAD')

    # Run for the day
    algo.run()