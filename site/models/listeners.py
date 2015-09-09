from sqlalchemy import event

from flask import g
from .core import SignallingSessionPlus as Session
from flask.signals import Namespace
from ..utils import (set_if_absent_and_get, append_if_absent, flatten,
                     ist_now)
from decimal import Decimal

db_signals = Namespace()
out_of_stock = db_signals.signal('out_of_stock')
shipment_status_change = db_signals.signal('shipment_status_change')
customer_init = db_signals.signal('customer_init')


def all_subclasses(cls):
    return cls.__subclasses__() + [
        g for s in cls.__subclasses__() for g in all_subclasses(s)]


def add_to_record(key, item, struct=g):
    append_if_absent(set_if_absent_and_get(struct, key, []), item)


class EventListener:

    def __init__(self, app=None):
        self.app = app
        if app:
            self.initialize_listeners()

    def init_app(self, app):
        self.app = app
        self.initialize_listeners()

    def initialize_listeners(self):
        event.listen(Shipment.status, 'set', self.on_shipment_status_change,
                     retval=True)
        event.listen(
            Shipment.total_cost, 'set', self.on_shipment_cost_change)
        event.listen(Claim.converted, 'set', self.on_claim_redeemed)
        event.listen(OrderItemPrintable_In_WarehouseEntry.qa_passed, 'set', self.on_qa_passed)
        # event.listen(Claim.revoked, 'set', self.on_claim_revoked)
        event.listen(SKU_In_Shipment, 'init', self.record_sku_addition)
        # event.listen(Customer, 'init', self.record_customer_init)
        event.listen(Shipment, 'init', self.record_shipment_init)
        # event.listen(MerchandiseOrderItemInShipment, 'init',
        #              self.record_merchandise_order_item_in_shipment_init)
        # for cls in [MerchandiseSKU] + all_subclasses(MerchandiseSKU):
        #     event.listen(cls, 'init', self.record_merchandise_sku_init)
        # for cls in [Printable]+all_subclasses(Printable):
        #     event.listen(cls, 'init', self.record_printable_init)

        # event.listen(db.mapper, 'after_insert', self.do_after_insert)
        # event.listen(db.mapper, 'after_update', self.do_after_update)
        event.listen(Session, 'before_commit', self.do_before_commit)
        event.listen(Campaign.active, 'set', self.on_campaign_activated, retval=True)
        # event.listen(Session, 'before_flush', self.do_before_flush)
        for cls in [SKU]+all_subclasses(SKU):
            event.listen(cls.to_be_shipped, 'set', self.on_out_of_stock)
            # event.listen(cls.stock_in_inventory, 'set', self.on_stock_change)
            # event.listen(cls, 'init', self.record_sku_init)

    def on_campaign_activated(self, campaign, value, oldvalue, initiator):
        if value:
            # if any(sku.available_stock == 0 for slot in campaign.slots
            #        for sku in slot.skus):
            if not campaign.has_sufficient_stock:
                campaign.activate_on_stock_arrival = True
                return False
            campaign.send_mails()
            Claim.query.filter(Claim.customer_on_hold == True,
                               Claim.campaign_id == campaign.id).update(
                             {'customer_on_hold': False})

            return value
        return value


    def on_out_of_stock(self, sku, value, oldvalue, initiator):
        if (sku.stock_in_inventory - value <= 0
                and sku.stock_in_inventory != 0 and value != 0):
            add_to_record('out_of_stock', sku)

    # def on_stock_change(self, sku, new_stock, old_stock, initiator):
    #     if new_stock:
    #         if old_stock is None:
    #             old_stock = 0
    #         difference = new_stock - old_stock
    #         if difference > 0:
    #             add_to_record('stock_addition', sku)

    def on_claim_redeemed(self, claim, converted,
                          converted_oldvalue, initiator):
        if converted:
            add_to_record('claims_redeemed', claim)


    # def on_claim_revoked(self, claim, revoked,
    #                      revoked_oldvalue, initiator):
    #     if revoked:
    #         add_to_record('claims_revoked', claim)

    def _reverse_return(self, shipment):
        if 'shipments_returned' in g and shipment in g.shipments_returned:
            g.shipments_returned.remove(shipment)
        for item in shipment.contents:
                item.sku.stock_in_inventory -= item.quantity

    # Here Be Dragons
    def on_shipment_status_change(self, shipment, status,
                                  oldstatus, initiator):
        """
        Dont ask!
        This should have been implemented as a state flow. Will
        clean up and implement later.
        """
        add_to_record('status_changed_shipments', shipment)
        if status in Shipment.INTERNAL_STATUSES:
            return status
        if status not in (Shipment.ALLOWED_STATUSES):
            raise Exception("Invalid status string")
        if status == 'in-queue' or status == 'packed':
            if str(oldstatus).lower() == 'cancelled':
                for item in shipment.contents:
                    item.sku.to_be_shipped += item.quantity
            elif str(oldstatus).lower() in [
                    'dispatched', 'in-transit', 'delivered', 'returned']:
                if str(oldstatus).lower() == 'returned':
                    self._reverse_return(shipment)
                elif str(oldstatus) == 'delivered':
                    if ('shipments_delivered' in g and
                            shipment in g.shipments_delivered):
                        g.shipments_delivered.remove(shipment)
                for item in shipment.contents:
                    item.sku.to_be_shipped += item.quantity
                    item.sku.stock_in_inventory += item.quantity
        elif (status == 'in-transit' or status == 'dispatched'):
            if str(oldstatus).lower() == 'cancelled':
                status = 'cancelled'
                return status
            elif str(oldstatus).lower() in ['in-queue', 'packed']:
                for item in shipment.contents:
                    item.sku.to_be_shipped -= item.quantity
                    item.sku.stock_in_inventory -= item.quantity
            elif str(oldstatus) == 'delivered':
                if ('shipments_delivered' in g and
                        shipment in g.shipments_delivered):
                    g.shipments_delivered.remove(shipment)
            elif str(oldstatus).lower() == 'returned':
                self._reverse_return(shipment)           
        elif status == 'delivered':
            if str(oldstatus).lower() == 'cancelled':
                status = 'cancelled'
                return status
            elif str(oldstatus).lower() in ['in-queue', 'packed']:
                for item in shipment.contents:
                    item.sku.to_be_shipped -= item.quantity
                    item.sku.stock_in_inventory -= item.quantity
            elif str(oldstatus).lower() == 'returned':
                self._reverse_return(shipment)
            add_to_record('shipments_delivered', shipment)
        elif (status == 'cancelled'):
            if (str(oldstatus).lower() in
               ['dispatched', 'in-transit', 'delivered', 'returned']):
                status = str(oldstatus).lower()
                return status
            else:
                for item in shipment.contents:
                    item.sku.to_be_shipped -= item.quantity
        elif status == 'returned':
            if (str(oldstatus).lower() not in
               ['dispatched', 'in-transit', 'delivered']):
                status = oldstatus
                return status
            else:
                if 'shipments_returned' not in g:
                    setattr(g, 'shipments_returned', [])
                if shipment not in g.shipments_returned and\
                        oldstatus != 'returned':
                    g.shipments_returned.append(shipment)
                for item in shipment.contents:
                        item.sku.stock_in_inventory += item.quantity
        return status

    def on_shipment_cost_change(self, shipment, cost,
                                cost_before,
                                initiator):
        if cost:
            if cost_before is None:
                cost_before = Decimal(0.0)
            amount = cost_before-cost
            if amount < 0:
                add_to_record('shipment_deductions', (shipment, amount))
            elif amount > 0:
                add_to_record('shipment_refunds', (shipment, amount))

    def on_qa_passed(self, item_in_warehouse_entry, value,
                     old_value, initiator):
        if value:
            if old_value is None:
                old_value = 0
            new_addition = value - old_value
            if new_addition != 0:
                add_to_record('ready_to_process', (
                    item_in_warehouse_entry, new_addition))

    def record_sku_addition(self, sku_in_shipment, args, kwargs):
        add_to_record('skus_to_be_shipped', sku_in_shipment)

    def record_shipment_init(self, shipment, args, kwargs):
        add_to_record('new_shipments', shipment)

    # def record_priceable_init(self, priceable, args, kwargs):
    #     add_to_record('new_priceables', priceable)



    # def record_sku_init(self, sku, args, kwargs):
    #     add_to_record('new_skus', sku)

    # def record_printable_init(self, printable, args, kwargs):
    #     add_to_record('new_printables', printable)

    def do_before_commit(self, session):
        # if 'new_skus' in g:
        #     for sk in g.new_skus:
        #         if sk.category is 'other_sku':
        #             sk.label = "U%s-OSKU-%s" % (
        #                 sk.user_id, sk.name.upper().replace(' ', '')[:20])
                # elif sk.category is 'tshirt_merchandise_sku':
                #     sk.label = "{merchandise_code}-{color}-{size}".format(
                #         merchandise_code=session.query(Merchandise).get(
                #             sk.merchandise_id).label,
                #         color=printable_params.tshirt_colors[sk.color],
                #         size=sk.size_abbr)
                # elif sk.category in ['sticker_merchandise_sku',
                #                      'sticker_sheet_merchandise_sku']:
                #     sk.label = "{merchandise_code}-{opacity}".format(
                #         merchandise_code=session.query(Merchandise).get(
                #             sk.merchandise_id).label,
                #         opacity=sk.opacity[0])
                # elif sk.category in ['poster_merchandise_sku',
                #                      'postcard_merchandise_sku']:
                #     sk.label = session.query(Merchandise).get(
                #         sk.merchandise_id).label
                # else:
                #     sk.label = session.query(Merchandise).get(
                #         sk.merchandise_id).label
            # g.new_skus = []
        # if 'new_printables' in g:
        #     for p in g.new_printables:
        #         if not p.label:
        #             p.label = session.query(Vendor).get(p.vendor_id).code
        #             for attr in p._label_attrs_order_:
        #                 if getattr(p, attr):
        #                     try:
        #                         p.label += '-' + session.query(
        #                             PrintableCode).filter_by(
        #                             attribute_type=attr,
        #                             attribute_value=getattr(
        #                                 p, attr)).one().attribute_code
        #                     except:
        #                         p.label += '-' + strip_bad_chars(getattr(
        #                             p, attr).upper())
        #                 else:
        #                     p.label += '-NA'
        #     g.new_printables = []
        if 'shipment_deductions' in g:
            for shipment, amount in g.shipment_deductions:
                # The amount here will be a negative value
                User.get(shipment.user_id).account_balance += amount
                ShipmentDeduction.build(
                    amount=amount, shipment_id=shipment.id,
                    user_id=shipment.user_id)
            g.shipment_deductions = []
        if 'ready_to_process' in g:
            for item_in_warehouse_entry, new_addition in g.ready_to_process:
                item_in_warehouse_entry.order_item_printable.ready_to_process += new_addition
            g.ready_to_process = []
        if 'shipment_refunds' in g:
            for shipment, amount in g.shipment_refunds:
                # The amount here will be a positive value
                User.get(shipment.user_id).account_balance += amount
                ShipmentRefund.build(
                    amount=amount, shipment_id=shipment.id,
                    user_id=shipment.user_id)
            g.shipment_refunds = []
        if 'status_changed_shipments' in g:
            for shipment in g.status_changed_shipments:
                shipment.last_acted_at = ist_now()
            del g.status_changed_shipments
        notifications = []
        if 'shipments_delivered' in g:
            notifications += ShipmentDeliveryService(
                ).build_all_from_shipments(g.shipments_delivered)
            g.shipments_delivered = []
        if 'claims_redeemed' in g:
            notifications += ClaimRedemptionService(
                ).build_all_from_claims(g.claims_redeemed)
            for claim in g.claims_redeemed:
                try:
                    if claim.user.has_claim_redemption_notify_hook:
                        post_claim_redemption(claim)
                except:
                    pass
            g.claims_redeemed = []
        
        # if 'claims_revoked' in g:
        #     notifications += ClaimRevocationService(
        #         ).build_all_from_claims(g.claims_revoked)
        #     g.claims_revoked = []
        # if 'new_priceables' in g:
        #     count_of_priceables = 
        #     for priceable in g.new_priceables:
        #         vendor_id = priceable.vendor_id or priceable.vendor.id
        #         canvas_id = priceable.canvas_id or priceable.canvas.id
        #         existing_count_of_priceables_from_vendor = session.query(
        #             Priceable).filter_by(
        #             vendor_id=vendor_id,
        #             canvas_id=canvas_id).count()
        #         if existing_count_of_priceables_from_vendor > 0:


        #     del g.new_priceables
        if 'skus_to_be_shipped' in g:
            for sis in g.skus_to_be_shipped:
                if (not hasattr(sis.shipment, 'with_order_item_instances') or
                        len(sis.shipment.with_order_item_instances) == 0):
                    sku = session.query(SKU).get(sis.sku_id)
                    if sku.available_stock > 0:
                        sku.to_be_shipped = sku.to_be_shipped + sis.quantity
                    else:
                        if not sku.to_be_shipped_on_stock_addition:
                            sku.to_be_shipped_on_stock_addition = 0
                        sku.to_be_shipped_on_stock_addition += sis.quantity
                        sis.waiting_for_stock_addition = True
                        sis.shipment.status = 'waiting_for_stock_addition'
            del g.skus_to_be_shipped
        session.add_all(notifications)

        alerts = []
        if 'shipments_returned' in g:
            while len(g.shipments_returned) > 0:
                shipment = g.shipments_returned.pop()
                alerts.append(ShipmentReturnedAlert(
                    shipment_id=shipment.id, user_id=shipment.user_id))
            del g.shipments_returned
        if 'out_of_stock' in g:
            while len(g.out_of_stock) > 0:
                sku = g.out_of_stock.pop()
                alerts.append(OutOfStockAlert(
                    sku_id=sku.id, user_id=sku.user_id))
                for sku_in_campaign_slot in sku.in_campaign_slot_instances:
                    campaign = sku_in_campaign_slot.campaign
                    if campaign.active:
                        other_skus = [
                            sk for sk in
                            sku_in_campaign_slot.campaign_slot.skus
                            if sk != sku]
                        if all(sk.available_stock == 0
                               for sk in other_skus):
                            campaign.active = False
                            campaign.activate_on_stock_arrival = True
                            break
            del g.out_of_stock
        session.add_all(alerts)
