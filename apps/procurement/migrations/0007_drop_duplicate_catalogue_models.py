"""Drop the duplicate catalogue models that were mistakenly registered under the
procurement app label in 0006, and add Criterion.description.

Migration 0006 created procurement.CatalogueItem / CatalogueQuote / PurchaseOrder
alongside the real workflow.CatalogueItem / CatalogueQuote / PurchaseOrder models.
Both sets declared different db_tables (procurement_catalogueitem vs cat_item), so
the duplicate trio was a shadow copy: empty, unreadable through the ORM once the
duplicate module was removed, and never written to. They are deleted here rather
than left as dead tables that a future reader would mistake for real data.

Deleting a model is a table DROP, so no field-level rewrite is emitted: SQLite
cannot rebuild a table for a model that no longer exists in the app registry.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("procurement", "0006_auctionbid_catalogueitem_cataloguequote_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="criterion",
            name="description",
            field=models.TextField(blank=True),
        ),
        migrations.DeleteModel(name="CatalogueQuote"),
        migrations.DeleteModel(name="PurchaseOrder"),
        migrations.DeleteModel(name="CatalogueItem"),
    ]
