from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, SubmitField, HiddenField
from wtforms.validators import DataRequired, Length

class TickerForm(FlaskForm):
    symbol = StringField(
        'Symbol',
        [DataRequired(), Length(max=20, message=('Too long'))]
    )

    exchange = StringField(
        'Exchange',
        [Length(max=20, message=('Too long'))]
    )

    asset = SelectField('Asset Class', choices=[('Stock', 'Stock'), ('Crypto', 'Crypto')])

    submit = SubmitField('Add')

