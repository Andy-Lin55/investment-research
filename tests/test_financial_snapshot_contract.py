"""Historical snapshots must retain comparable dimensions and Excel semantics."""
from io import BytesIO
import hashlib

import pytest
from openpyxl import Workbook

from research.financial_snapshot import from_workbook


SHEETS = ('利润表', '资产负债表', '现金流量表')


def book(metadata=True):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name in SHEETS:
        sheet = workbook.create_sheet(name)
        sheet['B2'] = '合成公司｜历史' + name
        if metadata:
            sheet['B3'] = '币种'; sheet['C3'] = 'USD'
            sheet['D3'] = '合并口径'; sheet['E3'] = '合并'
            sheet['F3'] = '原始金额单位'; sheet['G3'] = '元'
            sheet['B4'] = '原始股数单位'; sheet['C4'] = '股'
            sheet['D4'] = '历史数据来源'; sheet['E4'] = 'synthetic annual report p.10'
        sheet['B5'] = '科目'; sheet['C5'] = '展示单位'
        sheet['D5'] = '2025年度A'
        sheet['E5'] = '2026-06-30期末A' if name == '资产负债表' else '2026-06-30累计A'
        sheet['B6'] = '营业收入'; sheet['C6'] = '亿元'
        sheet['D6'] = 100; sheet['E6'] = 12.34
    return workbook


def snapshot(workbook):
    output = BytesIO(); workbook.save(output)
    data = output.getvalue()
    return from_workbook(data, issuer_id='issuer-a', expected_company='合成公司',
                         source={'sha256': hashlib.sha256(data).hexdigest()}, as_of='2026-10-29')


@pytest.mark.parametrize('fmt,stored,display', [
    ('0.00"%"', 12.34, 12.34),
    (r'0.00\%', 12.34, 12.34),
    ('0.00%', .1234, 12.34),
    ('0.00', 12.34, 12.34),
    ('0.00"%";0.00%', 12.34, 12.34),
    ('0.00%;0.00"%"', -12.34, -12.34),
    ('[>=1]0.00"%";[<1]0.00%', 12.34, 12.34),
    ('[>=1]0.00"%";[<1]0.00%', .1234, 12.34),
    ('0.00_%', 12.34, 12.34),
    ('0.00*%', 12.34, 12.34),
])
def test_only_active_percent_operator_scales_selected_numeric_section(fmt, stored, display):
    workbook = book()
    row = workbook['利润表']
    row['C6'] = '%'; row['D6'] = stored; row['D6'].number_format = fmt
    result = snapshot(workbook)['tables'][0]['rows'][0]
    assert result['values'][0] == pytest.approx(display)


def test_explicit_currency_scope_and_raw_display_dimensions_survive_without_conversion():
    result = snapshot(book())
    table = result['tables'][0]
    assert table['currency'] == 'USD'  # Chinese unit labels do not imply CNY.
    assert table['scope'] == 'consolidated'
    row = table['rows'][0]
    assert row['values'] == [100, 12.34]
    assert row['raw_values'] == [100, 12.34]
    assert row['dimensions'] == dict(currency='USD', scope='consolidated',
        raw_unit='元', raw_scale=1, display_unit='亿元', display_scale=100_000_000)
    assert table['metadata_cells']['currency'] == ['利润表!C3']
    assert table['historical_source'] == 'synthetic annual report p.10'


@pytest.mark.parametrize('cell,value,dimension', [
    ('C3', 'CNY', 'currency'), ('E3', '母公司', 'scope'),
])
def test_incompatible_statement_dimensions_are_rejected(cell, value, dimension):
    workbook = book(); workbook['资产负债表'][cell] = value
    with pytest.raises(ValueError, match=dimension):
        snapshot(workbook)


def test_conflicting_dimensions_inside_one_header_are_rejected():
    workbook = book(); workbook['利润表']['B4'] = '币种：CNY'
    with pytest.raises(ValueError, match='currency'):
        snapshot(workbook)


def test_unknown_metadata_and_missing_history_stay_explicit_without_inventing_currency():
    workbook = book(metadata=False)
    workbook['利润表']['E6'] = None
    result = snapshot(workbook)
    table = result['tables'][0]
    assert table['currency'] is None and table['scope'] is None
    dimensions = table['rows'][0]['dimensions']
    assert dimensions['raw_unit'] is None and dimensions['raw_scale'] is None
    assert dimensions['display_scale'] == 100_000_000
    assert table['rows'][0]['values'] == [100, None]
    assert {'利润表:missing_currency', '利润表:missing_scope',
            '利润表:missing_raw_amount_unit', '利润表:missing_historical_source',
            '利润表!E6:missing_cached_value'} <= set(result['gaps'])


@pytest.mark.parametrize('cell,value,dimension', [
    ('C3', 'unknown', 'currency'), ('E3', 'unknown', 'scope'), ('G3', 'unknown', 'raw_amount_unit'),
])
def test_unrecognized_dimension_metadata_is_flagged(cell, value, dimension):
    workbook = book(); workbook['利润表'][cell] = value
    result = snapshot(workbook)
    assert '利润表:unknown_' + dimension in result['gaps']


def test_inline_controlled_header_metadata_and_nonmoney_units_preserve_dimensions():
    workbook = book(metadata=False)
    for name in SHEETS:
        sheet = workbook[name]
        sheet['B3'] = '币种：HKD｜合并口径：母公司｜原始金额单位：万元｜原始股数单位：亿股｜原始每股单位：元/股'
        sheet['B7'] = '每股盈利'; sheet['C7'] = '元/股'; sheet['D7'] = 2.5
        sheet['B8'] = '股数'; sheet['C8'] = '亿股'; sheet['D8'] = 20
    result = snapshot(workbook)
    rows = result['tables'][0]['rows']
    assert rows[0]['dimensions'] == dict(currency='HKD', scope='parent', raw_unit='万元',
        raw_scale=10_000, display_unit='亿元', display_scale=100_000_000)
    assert rows[1]['dimensions'] == dict(currency='HKD', scope='parent', raw_unit='元/股',
        raw_scale=1, display_unit='元/股', display_scale=1)
    assert rows[2]['dimensions'] == dict(currency=None, scope='parent', raw_unit='亿股',
        raw_scale=100_000_000, display_unit='亿股', display_scale=100_000_000)


@pytest.mark.parametrize('name,label', [
    ('资产负债表', '2026-06-30累计A'),
    ('利润表', '2026-06-30期末A'),
    ('现金流量表', '2026-06-30期末A'),
    ('利润表', '2025-12-31期末A'),
    ('资产负债表', '2025-12-31累计A'),
])
def test_stock_and_flow_period_mismatches_are_rejected(name, label):
    workbook = book(); workbook[name]['E5'] = label
    with pytest.raises(ValueError, match='period basis'):
        snapshot(workbook)


def test_annual_periods_have_explicit_stock_or_flow_semantics():
    result = snapshot(book())
    assert result['tables'][0]['period_metadata'] == [
        dict(label='2025年度A', period_start='2025-01-01', period_end='2025-12-31', period_basis='year_to_date'),
        dict(label='2026-06-30累计A', period_start='2026-01-01', period_end='2026-06-30', period_basis='year_to_date')]
    assert result['tables'][1]['period_metadata'][0] == dict(
        label='2025年度A', period_start=None, period_end='2025-12-31', period_basis='instant')
    assert result['tables'][2]['period_metadata'][1]['period_basis'] == 'year_to_date'


def test_per_share_source_units_are_never_inferred_from_display_or_total_amount_units():
    workbook = book()
    sheet = workbook['利润表']
    sheet['B7'] = '每股盈利'; sheet['C7'] = '元/股'; sheet['D7'] = 2.5
    result = snapshot(workbook)
    row = result['tables'][0]['rows'][1]
    assert row['dimensions']['raw_unit'] is None
    assert row['dimensions']['raw_scale'] is None
    assert '利润表:missing_raw_per_share_unit' in result['gaps']
    sheet['F4'] = '原始每股单位'; sheet['G4'] = '元/股'
    row = snapshot(workbook)['tables'][0]['rows'][1]
    assert row['dimensions']['raw_unit'] == '元/股'
    assert row['dimensions']['raw_scale'] == 1


def test_absent_statement_periods_are_explicit_history_gaps():
    workbook = book()
    workbook['现金流量表']['E5'] = None
    workbook['现金流量表']['E6'] = None
    result = snapshot(workbook)
    assert '现金流量表:missing_period:2026-06-30' in result['gaps']
    assert result['tables'][2]['periods'] == ['2025年度A']
