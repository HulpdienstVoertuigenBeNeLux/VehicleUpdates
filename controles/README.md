# controles/

Controles op de sheets die in de uurlijkse run (`run-hulpdienstvoertuigenbenelux.yml`) draaien.

- `kolom_o.py` — kolom O (DataType): rij met ID moet Locatie/Voertuig/- hebben, "-" mag geen foto hebben.
  Melding per land naar `DISCORD_WEBHOOK_URL_REGIONS` (alleen nieuwe gevallen), rode voorwaardelijke opmaak in de sheets via het serviceaccount.
  Elke maandag vanaf 09:00 een weekoverzicht: totaal per land + CSV per land, of "er zijn geen fouten".

Sheets zonder ID's (Brandweer DE, Ambulance DE): in `config/sheets.json` `"rij": "inhoud"` — dan telt een rij mee als kolom A, B of D gevuld is.

Nieuwe sheet erbij: regel toevoegen in `config/sheets.json`, secret met het sheet-ID aanmaken, het secret in de
workflowstap doorgeven en de sheet als Bewerker delen met het serviceaccount.
