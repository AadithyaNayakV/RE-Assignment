"""Builds the two SYNTHETIC manual-test workbooks (test_clean and test_errors).

    python scripts/make_test_workbooks.py

Run from the repo root: paths are relative to the working directory, so it writes
data/clients/test_clean/source/test_clean.xlsx and
data/clients/test_errors/source/test_with_errors.xlsx. Their configs are
config/clients/test_clean.yaml and config/clients/test_errors.yaml.
"""
import calendar, datetime as dt
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment

months=[(2025,m) for m in range(1,13)]+[(2026,1)]
n=len(months)
lab=lambda y,m: dt.date(y,m,1).strftime('%b %Y')
subs=[40000+1000*i for i in range(n)]
svc=[8000]*n; svc[-1]=9000
host=[6000+100*i for i in range(n)]
supp=[3000]*n
pay=[25000]*6+[26000]*(n-6)
mkt=[5000]*n; mkt[-1]=8000
soft=[2000]*n
rent=[3000]*n

def build(path, errors, title):
    S=dict(subs=list(subs),svc=list(svc),host=host,supp=supp,pay=pay,mkt=list(mkt),soft=soft,rent=rent)
    bad=[0]*n
    total_off=None
    if errors:
        S['svc'][11]=0
        bad[-1]=-1500
        total_off=(7,5000)
    idx=list(range(n))
    if errors: idx=[i for i in idx if i!=5]   # drop Jun 2025
    cols=list(reversed(idx))
    wb=Workbook(); ws=wb.active; ws.title='Income Statement'
    ws['A1']=title; ws['A1'].font=Font(bold=True,size=13)
    ws['A2']='Income Statement'; ws['A3']='SYNTHETIC test data'
    for j,i in enumerate(cols):
        c=ws.cell(row=5,column=3+j,value=lab(*months[i])); c.font=Font(bold=True); c.alignment=Alignment(horizontal='right')
    r=[6]
    def sec(label):
        ws.cell(row=r[0],column=1,value=label).font=Font(bold=True); r[0]+=1
    def item(label,fn):
        ws.cell(row=r[0],column=2,value=label)
        for j,i in enumerate(cols):
            c=ws.cell(row=r[0],column=3+j,value=fn(i)); c.number_format='#,##0;-#,##0'
        r[0]+=1; return r[0]-1
    def tot(label,fn):
        ws.cell(row=r[0],column=1,value=label).font=Font(bold=True)
        for j,i in enumerate(cols):
            c=ws.cell(row=r[0],column=3+j,value=fn(i)); c.number_format='#,##0;-#,##0'; c.font=Font(bold=True)
        r[0]+=1; return r[0]-1
    rows={}
    sec('Income')
    rows['subs']=item('Subscriptions',lambda i:S['subs'][i])
    rows['svc']=item('Services',lambda i:S['svc'][i])
    inc=lambda i:S['subs'][i]+S['svc'][i]
    rows['tinc']=tot('Total Income',lambda i:inc(i)+(total_off[1] if total_off and i==total_off[0] else 0))
    r[0]+=1
    sec('Cost of Goods Sold')
    item('Hosting',lambda i:S['host'][i]); item('Support',lambda i:S['supp'][i])
    cg=lambda i:S['host'][i]+S['supp'][i]
    tot('Total Cost of Goods Sold',cg)
    r[0]+=1
    rows['gp']=tot('Gross Profit',lambda i:inc(i)-cg(i))
    r[0]+=1
    sec('Operating Expenses')
    item('Payroll',lambda i:S['pay'][i])
    rows['mkt']=item('Marketing',lambda i:S['mkt'][i])
    item('Software',lambda i:S['soft'][i])
    rows['rent']=item('Rent',lambda i:S['rent'][i])
    if errors: rows['bad']=item('Bad Debts',lambda i:bad[i])
    ox=lambda i:S['pay'][i]+S['mkt'][i]+S['soft'][i]+S['rent'][i]+(bad[i] if errors else 0)
    rows['topex']=tot('Total Operating Expenses',ox)
    r[0]+=1
    rows['oi']=tot('Operating Income',lambda i:inc(i)-cg(i)-ox(i))
    tot('Net Income',lambda i:inc(i)-cg(i)-ox(i))
    if errors:
        col=lambda i:3+cols.index(i)
        ws.cell(row=rows['mkt'],column=col(9),value='n/a')      # Oct 2025 text
        ws.cell(row=rows['rent'],column=col(10)).value=None     # Nov 2025 blank
    ws.column_dimensions['A'].width=28; ws.column_dimensions['B'].width=16
    wb.save(path); return rows,cols

import os
def main():
    for client, fname, title, errors in [
        ('test_clean','test_clean.xlsx','Test Clean Co (SYNTHETIC)',False),
        ('test_errors','test_with_errors.xlsx','Test Errors Co (SYNTHETIC)',True)]:
        folder=os.path.join('data','clients',client,'source')
        os.makedirs(folder,exist_ok=True)
        path=os.path.join(folder,fname)
        build(path,errors,title)
        print('written:',os.path.abspath(path),os.path.getsize(path),'bytes')

if __name__ == '__main__':
    main()
