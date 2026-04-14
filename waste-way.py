import math
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import streamlit as st
from scipy.interpolate import RegularGridInterpolator
import os

# --- 한글 깨짐 방지 및 폰트 설정 ---
def set_korean_font():
    if os.name == 'nt': 
        plt.rc('font', family='Malgun Gothic')
    else: 
        plt.rc('font', family='AppleGothic')
    plt.rcParams['axes.unicode_minus'] = False 

# ==========================================
# 1. 데이터 로드 및 보간 함수 (준설선 데이터만 유지)
# ==========================================
@st.cache_data
def load_dredging_data(file_path):
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        full_path = os.path.join(current_dir, file_path)
        df = pd.read_csv(full_path, sep='\t', encoding='cp949')
        distances = df.iloc[1:, 0].astype(float).values
        n_values = np.array([0, 5, 10, 15, 20, 25])
        q_matrix = df.iloc[1:, 1:7].astype(float).values
        interp_func = RegularGridInterpolator((distances, n_values), q_matrix, method='linear', bounds_error=False, fill_value=None)
        return distances, n_values, q_matrix, interp_func
    except Exception as e:
        st.error(f"⚠️ 준설선 데이터 파일 로드 실패: {e}")
        return None, None, None, None

# ==========================================
# 2. 설계 계산 클래스
# ==========================================
class SpillwayDesign:
    def calculate_qs(self, q_val, p_kw, f_factor, efficiency):
        b0_kw = p_kw * f_factor
        qs = q_val * (b0_kw / 746) * efficiency
        return b0_kw, qs

    def calculate_tc_kerby(self, L, n_kerby, S):
        t_min = 1.44 * ((L * n_kerby) / (S**0.5))**0.467
        return t_min

    def calculate_discharge(self, area_ha, t_min, runoff_coeff, dredger_count, qs_val, mud_ratio, ksce_data, poly_coeffs, gen_coeffs):
        water_ratio = (1 - mud_ratio) / mud_ratio
        dredge_q = (qs_val * dredger_count * (1 + water_ratio)) / 3600.0
        
        # ① 대한토목학회 식
        if ksce_data["formula_type"] == "talbot":
            rain_i_tomok = ksce_data["a"] / (t_min + ksce_data["b"])
        elif ksce_data["formula_type"] == "japanese":
            rain_i_tomok = ksce_data["a"] / (math.sqrt(t_min) + ksce_data["b"])
        elif ksce_data["formula_type"] == "power":
            rain_i_tomok = ksce_data["a"] / (t_min ** ksce_data["n"])
        else:
            rain_i_tomok = 0.0

        # ② 전대수다항식
        t_hr = t_min / 60.0
        ln_t = math.log(t_hr)
        a1, b1, c1, d1, e1, f1, g1 = poly_coeffs
        ln_I = a1 + b1*(ln_t) + c1*(ln_t**2) + d1*(ln_t**3) + e1*(ln_t**4) + f1*(ln_t**5) + g1*(ln_t**6)
        rain_i_poly = math.exp(ln_I)
        
        # ③ General 식 (a, b, n)
        ga, gb, gn = gen_coeffs
        rain_i_gen = ga / (t_min**gn + gb)
        
        return rain_i_tomok, rain_i_poly, rain_i_gen, dredge_q, water_ratio

    def calculate_dimensions(self, Q, B, n_coeff, S):
        y = 0.5 
        for _ in range(100):
            A = B * y
            P = B + 2 * y
            R = A / P
            V = (1/n_coeff) * (R**(2/3)) * (S**0.5)
            Q_calc = A * V
            if abs(Q - Q_calc) < 0.001: break
            y = y * (Q / Q_calc)**0.6
        return {'y': y, 'V': V, 'A': A, 'R': R}

# ==========================================
# 3. 메인 인터페이스
# ==========================================
def main():
    st.set_page_config(page_title="여수토 단면 설계", layout="wide")
    set_korean_font() 
    
    # 데이터 로드
    distances, n_values, q_matrix, interp_func = load_dredging_data('dredging_capacity.csv')
    design = SpillwayDesign()

    if interp_func is None: st.stop()

    # --- 사이드바: 상태 표시 (엑셀 연동 경고 삭제 및 수동 모드 안내) ---
    with st.sidebar:
        st.header("📁 강우 데이터 설정")
        st.success("💡 **수동 입력 모드 활성화됨**\n\n엑셀 자동 연동 대신 WAMIS 사이트를 통한 직관적인 수동 계수 입력 방식으로 전환되었습니다.")

    st.title("🌊 여수토 단면 및 본체 설계")

    # ------------------------------------------
    # 1. 준설선 성능 산정
    # ------------------------------------------
    st.header("1. 펌프준설선 시간당 준설량(Qs) 산정")
    col1, col2 = st.columns([1, 1.2])
    
    with col1:
        with st.container(border=True):
            st.markdown("#### 📍 입력 파라미터")
            input_dist = st.number_input("배송거리 (km)", value=8.0, step=0.1)
            input_n = st.number_input("N값 (토사경도)", value=5, step=1)
            
            st.divider()
            dredger_hp = st.number_input("펌프준설선 규격 (HP)", value=20000.0)
            p_kw_auto = dredger_hp * 0.7457 
            st.info(f"⚡ 주기관 정격출력 (자동 계산): **{p_kw_auto:,.2f} kW**")
            
            engine_type = st.selectbox("기관 종류 (환산계수 f)", ["디젤 (0.8)", "터빈 (0.9)"])
            f_factor = 0.8 if "디젤" in engine_type else 0.9
            
            st.markdown("**작업 효율 (E) 세부 입력**")
            e1 = st.selectbox("E1 (흙의 두께)", [1.00, 0.85, 0.75], index=1, format_func=lambda x: f"{x} (적당/얇음)")
            e2 = st.selectbox("E2 (평면 형상)", [1.10, 1.00, 0.90], index=1, format_func=lambda x: f"{x} (적당/산재)")
            e3 = st.selectbox("E3 (단면 형상)", [1.10, 1.00, 0.90], index=0, format_func=lambda x: f"{x} (평탄/변화)")
            e4 = st.selectbox("E4 (기타 여건)", [1.10, 1.00, 0.90], index=2, format_func=lambda x: f"{x} (보통/나쁨)")
            
            eff_total = e1 * e2 * e3 * e4
            st.write(f"✅ 종합 작업효율 (E) = **{eff_total:.2f}**")

    q_read = float(interp_func([input_dist, input_n])[0])
    b0_kw_res, qs_res = design.calculate_qs(q_read, p_kw_auto, f_factor, eff_total)

    with col2:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for i, n in enumerate(n_values):
            ax.plot(distances, q_matrix[:, i], label=f'N={n}', alpha=0.3, linestyle='--')
        
        ax.axvline(x=input_dist, color='red', linestyle='--', linewidth=0.8, alpha=0.5)
        ax.axhline(y=q_read, color='red', linestyle='--', linewidth=0.8, alpha=0.5)
        ax.scatter(input_dist, q_read, color='red', s=100, zorder=5)
        ax.annotate(f' 독취 q={q_read:,.2f}', (input_dist, q_read), color='red', fontweight='bold')
        
        ax.set_xticks(np.arange(math.floor(min(distances)), math.ceil(max(distances)) + 1, 1.0))
        ax.set_xlabel("배송거리 (km)")
        ax.set_ylabel("q (m3/hr)")
        ax.legend(loc='upper right', fontsize='x-small'); ax.grid(True, linestyle=':', alpha=0.6)
        st.pyplot(fig)
        st.markdown("<center><b>(그림) 펌프준설선 전동환산표 (한국농지개발연구소)</b></center>", unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown("#### 📝 효율 및 Qs 산정식")
            st.latex(rf"b_0 = P \times f = {p_kw_auto:,.2f} \times {f_factor} = {b0_kw_res:,.2f} \, kW")
            st.latex(rf"Q_s = q \times \frac{{b_0}}{{746}} \times E = {q_read:,.2f} \times \frac{{{b0_kw_res:,.2f}}}{{746}} \times {eff_total:.2f} = {qs_res:,.2f} \, m^3/hr")
            st.success(f"🏆 결과: $Q_s = {qs_res:,.2f} \, m^3/hr$")

    # ------------------------------------------
    # 2. 계획 유출량 산정
    # ------------------------------------------
    st.divider()
    st.header("2. 계획 유출량 산정")
    
    # --- 가. 여수토 유입량 산정 ---
    st.markdown("#### 가. 여수토 유입량 산정 (준설선)")
    c2_1, c2_2 = st.columns(2)
    with c2_1:
        d_count = st.number_input("준설선 대수 (N, 대)", value=2) 
    with c2_2:
        m_ratio = st.number_input("함니율 (R_m, mud ratio)", value=0.15) 

    calc_water_ratio = (1 - m_ratio) / m_ratio
    calc_dredge_q = (qs_res * d_count * (1 + calc_water_ratio)) / 3600.0

    with st.container(border=True):
        st.write("준설토(토석+물) 투기에 따른 여수토 유입량을 산정합니다.")
        st.latex(r"W (\text{함수비}) = \frac{1 - R_m}{R_m}")
        st.write(f"- $W = \\frac{{1 - {m_ratio}}}{{{m_ratio}}} = $ **{calc_water_ratio:.2f}**")
        
        st.divider()
        st.latex(r"Q_1 = \frac{Q_s \times N \times (1 + W)}{3600}")
        st.caption(f"적용 변수: 시간당 준설량 $Q_s$ = {qs_res:,.2f} m³/hr, 준설선 대수 $N$ = {d_count} 대")
        
        st.write(f"👉 **여수토 유입량 ($Q_1$) 상세 산출:**")
        st.latex(rf"Q_1 = \frac{{{qs_res:,.2f} \times {d_count} \times (1 + {calc_water_ratio:.2f})}}{{3600}} = {calc_dredge_q:,.3f} \, m^3/sec")

    # --- 나. 도달시간 산정 ---
    st.markdown("#### 나. 도달시간 산정 (Kerby 공식)")
    
    kerby_n_options = {
        "매끄러운 불투수표면": 0.02,
        "매끄러운 나지": 0.1,
        "경작지나 기복이 있는 나지": 0.2,
        "초지 또는 잔디": 0.4,
        "활엽수": 0.5,
        "침엽수, 깊은 표토층을 가진 활엽수림지대": 0.8
    }
    
    c2_t1, c2_t2, c2_t3 = st.columns([1, 1.2, 1])
    with c2_t1:
        kerby_L = st.number_input("사면거리/유로연장 (L, m)", value=1206.0)
    with c2_t2:
        selected_n_desc = st.selectbox("표면 형태 선택 (지체계수 n)", list(kerby_n_options.keys()), index=0)
        kerby_n = kerby_n_options[selected_n_desc]
        st.caption(f"적용 계수 $n$ = **{kerby_n}**")
    with c2_t3:
        kerby_S = st.number_input("사면경사 (S)", value=0.001, format="%.4f")
    
    t_min_raw = design.calculate_tc_kerby(kerby_L, kerby_n, kerby_S)
    t_min_calc = round(t_min_raw, 2)
    
    with st.container(border=True):
        st.write("Kerby 공식을 이용하여 유역의 도달시간(유입시간)을 산정합니다.")
        st.latex(r"t_c = 1.44 \times \left( \frac{L \times n}{\sqrt{S}} \right)^{0.467}")
        st.caption(f"적용 변수: 사면거리 $L$ = {kerby_L} m, 지체계수 $n$ = {kerby_n}, 사면경사 $S$ = {kerby_S}")
        
        st.divider()
        st.write(f"👉 **도달시간 ($t_c$) 상세 산출:**")
        st.latex(rf"t_c = 1.44 \times \left( \frac{{{kerby_L} \times {kerby_n}}}{{\sqrt{{{kerby_S}}}}} \right)^{{0.467}} = {t_min_raw:.2f} \, \text{{min}}")
        st.info(f"⏳ **적용 유입시간 ($t$) = {t_min_calc} 분** (소수점 2자리 반올림)")
        
    # --- 다. 강우에 의한 계획 유출량 산정 ---
    st.markdown("#### 다. 강우에 의한 계획 유출량 산정")
    
    # 1) 대한토목학회 데이터
    ksce_region_data = {
        "서울, 인천, 수원, 춘천, 원주, 제천, 충주": {
            "5년": {"formula_type": "power", "a": 520.0, "n": 0.58, "latex": r"\frac{520}{t^{0.58}}"},
            "10년": {"formula_type": "power", "a": 612.0, "n": 0.58, "latex": r"\frac{612}{t^{0.58}}"},
            "20년": {"formula_type": "power", "a": 697.0, "n": 0.58, "latex": r"\frac{697}{t^{0.58}}"}
        },
        "강릉, 포항, 대구": {
            "5년": {"formula_type": "japanese", "a": 239.0, "b": -1.60, "latex": r"\frac{239}{\sqrt{t} - 1.60}"},
            "10년": {"formula_type": "japanese", "a": 289.0, "b": -1.25, "latex": r"\frac{289}{\sqrt{t} - 1.25}"},
            "20년": {"formula_type": "japanese", "a": 338.0, "b": -1.45, "latex": r"\frac{338}{\sqrt{t} - 1.45}"}
        },
        "청주, 군산, 전주": {
            "5년": {"formula_type": "japanese", "a": 306.0, "b": -2.76, "latex": r"\frac{306}{\sqrt{t} - 2.76}"},
            "10년": {"formula_type": "japanese", "a": 360.0, "b": -2.81, "latex": r"\frac{360}{\sqrt{t} - 2.81}"},
            "20년": {"formula_type": "japanese", "a": 410.0, "b": -2.86, "latex": r"\frac{410}{\sqrt{t} - 2.86}"}
        },
        "광주, 여수, 목포, 마산, 부산": {
            "5년": {"formula_type": "power", "a": 581.0, "n": 0.50, "latex": r"\frac{581}{t^{0.50}}"},
            "10년": {"formula_type": "power", "a": 678.0, "n": 0.50, "latex": r"\frac{678}{t^{0.50}}"},
            "20년": {"formula_type": "power", "a": 766.0, "n": 0.50, "latex": r"\frac{766}{t^{0.50}}"}
        }
    }

    st.markdown("##### 1) 강우강도 산정")
    c2_3, c2_4, c2_5 = st.columns(3)
    with c2_3:
        area_ha = st.number_input("유역면적 (A, ha)", value=103.24)
        run_c = st.number_input("유출계수 (C)", value=1.0)
    with c2_4:
        st.write("① 대한토목학회(1980)")
        selected_region = st.selectbox("지역 선택", list(ksce_region_data.keys()), index=2) 
        selected_ksce_period = st.selectbox("빈도 선택 (대한토목학회)", ["5년", "10년", "20년"], index=1)
        ksce_data = ksce_region_data[selected_region][selected_ksce_period]
            
    # --- ✨ 수동 입력 방식으로 변경된 부분 ---
    with c2_5:
        st.write("② 전대수다항식 / ③ GENERAL식")
        st.markdown("**[🔗 전국 하천유역 홍수량 및 확률강우량 정보 (WAMIS)](https://map.wamis.go.kr/)**", help="클릭 시 새 창에서 열립니다. 해당 사이트에서 지역별 계수를 확인하여 아래에 직접 입력해 주세요.")
        
        with st.container(border=True):
            st.markdown("###### 📍 지역 및 빈도 설정")
            c_loc1, c_loc2 = st.columns(2)
            with c_loc1:
                manual_station_name = st.text_input("지역이름", value="군산")
            with c_loc2:
                # 💡 이미지에 표시된 관측소 코드번호로 업데이트
                manual_station_code = st.text_input("코드번호", value="32031140")
                
            selected_period_str = st.selectbox("재현빈도 선택", ["5년", "10년", "20년"], index=1)
            selected_period = selected_period_str.replace("년", "")
            
            selected_station_label = f"{manual_station_name}({manual_station_code})"

        with st.expander("📝 전대수다항식 계수 입력", expanded=False):
            st.caption("a, b, c, d, e, f, g 계수를 입력해 주세요.")
            c_pa, c_pb, c_pc = st.columns(3)
            # 💡 첨부 이미지에 표시된 a~g 계수값으로 기본값 업데이트
            p_a = c_pa.number_input("a", value=4.103589, format="%.6f", key="pa")
            p_b = c_pb.number_input("b", value=-0.345960, format="%.6f", key="pb")
            p_c = c_pc.number_input("c", value=-0.282630, format="%.6f", key="pc")
            
            c_pd, c_pe, c_pf, c_pg = st.columns(4)
            p_d = c_pd.number_input("d", value=0.276805, format="%.6f", key="pd")
            p_e = c_pe.number_input("e", value=-0.146250, format="%.6f", key="pe")
            p_f = c_pf.number_input("f", value=0.033644, format="%.6f", key="pf")
            p_g = c_pg.number_input("g", value=-0.002780, format="%.6f", key="pg")
            
            selected_poly_coeffs = [p_a, p_b, p_c, p_d, p_e, p_f, p_g]

        with st.expander("📝 GENERAL식 계수 입력", expanded=False):
            st.caption("해당 빈도의 a, b, n 계수를 입력해 주세요.")
            c_ga, c_gb, c_gn = st.columns(3)
            g_a = c_ga.number_input("a", value=1047.202, format="%.3f", key="ga")
            g_b = c_gb.number_input("b", value=7.60767, format="%.5f", key="gb")
            g_n = c_gn.number_input("n", value=0.67501, format="%.5f", key="gn")
            
            selected_gen_coeffs = [g_a, g_b, g_n]

    # 계산 수행
    rain_i_tomok, rain_i_poly, rain_i_gen, dredge_q, water_ratio = design.calculate_discharge(
        area_ha, t_min_calc, run_c, d_count, qs_res, m_ratio, ksce_data, selected_poly_coeffs, selected_gen_coeffs
    )

    with st.container(border=True):
        st.write(f"**① 대한토목학회 (1980)** [{selected_region} - {selected_ksce_period} 빈도 적용]")
        st.latex(rf"I = {ksce_data['latex']}")
        
        if ksce_data["formula_type"] == "talbot":
            b_val = ksce_data['b']
            b_sign = "+" if b_val >= 0 else "-"
            st.latex(rf"I = \frac{{{ksce_data['a']}}}{{{t_min_calc} {b_sign} {abs(b_val)}}}")
        elif ksce_data["formula_type"] == "japanese":
            b_val = ksce_data['b']
            b_sign = "+" if b_val >= 0 else "-"
            st.latex(rf"I = \frac{{{ksce_data['a']}}}{{\sqrt{{{t_min_calc}}} {b_sign} {abs(b_val)}}}")
        elif ksce_data["formula_type"] == "power":
            st.latex(rf"I = \frac{{{ksce_data['a']}}}{{{t_min_calc}^{{{ksce_data['n']}}}}}")
            
        st.write(f"👉 산정된 강우강도: **{rain_i_tomok:.2f} mm/hr**")
        
        st.divider()
        st.write(f"**② 전대수다항식** [{selected_station_label} - {selected_period}년 빈도 적용]")
        st.latex(r"\ln(I) = a + b(\ln t) + c(\ln t)^2 + d(\ln t)^3 + e(\ln t)^4 + f(\ln t)^5 + g(\ln t)^6")
        
        a1, b1, c1, d1, e1, f1, g1 = selected_poly_coeffs
        st.write(f"📌 **입력된 계수:** `a={a1:.6f}`, `b={b1:.6f}`, `c={c1:.6f}`, `d={d1:.6f}`, `e={e1:.6f}`, `f={f1:.6f}`, `g={g1:.6f}`")
        
        st.write(f"👉 산정된 강우강도: **{rain_i_poly:.2f} mm/hr**")

        st.divider()
        st.write(f"**③ GENERAL식** [{selected_station_label} - {selected_period}년 빈도 적용]")
        st.latex(r"I = \frac{a}{t^n + b}")
        ga, gb, gn = selected_gen_coeffs
        st.write(f"📌 **입력된 계수:** `a={ga:.6f}`, `b={gb:.6f}`, `n={gn:.6f}`")
        st.write(f"👉 산정된 강우강도: **{rain_i_gen:.2f} mm/hr**")

    st.markdown("##### 2) 강우강도에 대한 투기장 유역 계획유출량 산정")
    
    q_tomok = (1 / 3.6) * run_c * rain_i_tomok * (area_ha / 100)
    q_poly = (1 / 3.6) * run_c * rain_i_poly * (area_ha / 100)
    q_gen = (1 / 3.6) * run_c * rain_i_gen * (area_ha / 100)
    
    with st.container(border=True):
        st.write("합리식(Rational Method)을 적용하여 각 강우강도별 유출량을 산정합니다.")
        st.latex(r"Q = \frac{1}{3.6} \times C \times I \times A")
        st.caption(f"적용 변수: 유출계수 $C$ = {run_c}, 유역면적 $A$ = {area_ha} ha (= {area_ha/100:.4f} km²)")
        
        st.write(f"**① 대한토목학회 유출량 산정:**")
        st.latex(rf"Q = \frac{{1}}{{3.6}} \times {run_c} \times {rain_i_tomok:.2f} \times {area_ha/100:.4f} = {q_tomok:.3f} \, m^3/sec")
        
        st.write(f"**② 전대수다항식 유출량 산정:**")
        st.latex(rf"Q = \frac{{1}}{{3.6}} \times {run_c} \times {rain_i_poly:.2f} \times {area_ha/100:.4f} = {q_poly:.3f} \, m^3/sec")
        
        st.write(f"**③ GENERAL식 유출량 산정:**")
        st.latex(rf"Q = \frac{{1}}{{3.6}} \times {run_c} \times {rain_i_gen:.2f} \times {area_ha/100:.4f} = {q_gen:.3f} \, m^3/sec")

    max_rain_i = max(rain_i_tomok, rain_i_poly, rain_i_gen)
    rain_q_max = max(q_tomok, q_poly, q_gen)
    design_q_final = max(rain_q_max, dredge_q)

    st.write("📊 **강우강도 산정 공식별 유출량 비교표**")
    df_runoff = pd.DataFrame({
        "산정 공식": ["① 대한토목학회", "② 전대수다항식", "③ GENERAL식"],
        "강우강도 I (mm/hr)": [f"{rain_i_tomok:.2f}", f"{rain_i_poly:.2f}", f"{rain_i_gen:.2f}"],
        "계획유출량 Q (m³/sec)": [f"{q_tomok:.3f}", f"{q_poly:.3f}", f"{q_gen:.3f}"]
    })
    st.table(df_runoff.set_index("산정 공식"))
    st.info(f"💡 산정된 강우 유출량 중 최대치 적용: **{rain_q_max:.3f} m³/sec**")

    # --- 라. 계획유출량 산정 결과 ---
    st.divider()
    st.markdown("#### 라. 계획유출량 산정 결과")
    with st.container(border=True):
        st.write("준설에 의한 유출량과 강우에 의한 최대 계획 유출량 중 큰 값을 여수토 규모 산정에 최종 적용합니다.")
        
        df_final_compare = pd.DataFrame({
            "구분": ["가. 준설선 유입량 (Q1)", "다. 강우 유출량 (Q2)"],
            "유출량 (m³/sec)": [f"{calc_dredge_q:,.3f}", f"{rain_q_max:,.3f}"],
            "산출 근거": ["준설능력 및 함수비 고려 산정", f"최대 강우강도({max_rain_i:.2f} mm/hr) 적용"]
        })
        st.table(df_final_compare.set_index("구분"))
        
        st.success(f"🏆 **최종 계획 유출량 ($Q_{{design}}$)** = Max($Q_1$, $Q_2$) = **{design_q_final:,.3f} m³/sec**")

    # ------------------------------------------
    # 3. 여수토 단면계획
    # ------------------------------------------
    st.divider()
    st.header("3. 여수토 단면계획")

    # --- 가. 여수토관 용량검토 ---
    st.markdown("### 가. 여수토관 용량검토")
    
    st.markdown("#### 1) 자연유하시 여수토관 최대 유량 및 유속 (Manning 공식)")
    c3_1, c3_2, c3_3 = st.columns(3)
    with c3_1:
        pipe_D = st.number_input("여수토관 관경 (D, m)", value=1.20, format="%.2f") 
    with c3_2:
        pipe_n = st.number_input("관 조도계수 (n)", value=0.024, format="%.3f")
    with c3_3:
        pipe_S = st.number_input("관거 경사 (S)", value=0.025, format="%.3f")
        
    with st.expander("📖 참고: 관재질에 따른 조도계수(n) 표 보기"):
        st.markdown("""
        | 대분류 | 단면 재질 | 조도계수(n) | 대분류 | 단면 재질 | 조도계수(n) |
        | :--- | :--- | :--- | :--- | :--- | :--- |
        | **관거** | 시멘트관 | 0.011~0.015 | **점토** | 도관 | 0.011~0.015 |
        | | 벽돌 | 0.013~0.017 | | 깔판 | 0.013~0.017 |
        | | 주철관 | 0.011~0.015 | **인공수로** | 아스팔트 | 0.013~0.017 |
        | | **콘크리트** | | | 벽돌 | 0.012~0.018 |
        | | - 매끄러운 표면 | 0.012~0.014 | | 콘크리트 | 0.011~0.020 |
        | | - 거친 표면 | 0.015~0.017 | | 자갈 | 0.020~0.035 |
        | | - 콘크리트관 | 0.011~0.015 | | 식물 | 0.030~0.040 |
        | | **주름형의 금속관(파형강관)** | | | | |
        | | - 보통관 | 0.022~0.026 | | | |
        | | - 포장된 인버트 | 0.018~0.022 | | | |
        | | - 아스팔트 라인 | 0.011~0.015 | | | |
        | | 플라스틱관(매끄러운) | 0.011~0.015 | | | |
        """)

    pipe_A = (math.pi * pipe_D**2) / 4
    pipe_R = pipe_D / 4  
    pipe_V_man = (1 / pipe_n) * (pipe_R**(2/3)) * (pipe_S**0.5)
    pipe_Q_man = pipe_A * pipe_V_man

    with st.container(border=True):
        st.write("여수토관이 조위보다 높은 경우, Manning 공식을 이용하여 통수능력을 검토합니다.")
        st.latex(r"V = \frac{1}{n} R^{2/3} S^{1/2}, \quad Q = A \cdot V")
        st.write(f"- 단면적($A$) = {pipe_A:.3f} m² | 경심($R$) = {pipe_R:.3f} m | 적용 조도계수($n$) = {pipe_n:.3f}")
        st.success(f"👉 **자연유하 최대 유속($V$)** = {pipe_V_man:.3f} m/s  \n👉 **자연유하 최대 유량($Q$)** = {pipe_Q_man:.3f} m³/s")

    st.markdown("#### 2) 고조시 배출 가능한 최대 유량 (조위보다 낮은 경우)")
    
    c3_4, c3_5 = st.columns(2)
    with c3_4:
        delta_H = st.number_input("허용 수위차 (ΔH, m)", value=3.64, format="%.2f")
    with c3_5:
        pipe_L = st.number_input("여수토관 연장 (L, m)", value=26.02, format="%.2f")
        
    st.markdown("##### 🔸 손실계수 산정")
    c3_f1, c3_f2, c3_f3 = st.columns(3)
    with c3_f1:
        f1_coeff = st.number_input("유입손실계수 (f₁)", value=0.50, format="%.2f")
    with c3_f3:
        f3_coeff = st.number_input("유출손실계수 (f₃)", value=1.00, format="%.2f")
        
    f2_coeff = 124.5 * (pipe_n**2) / (pipe_D**(4/3)) * pipe_L
    total_loss_coeff = f1_coeff + f2_coeff + f3_coeff
    
    with c3_f2:
        st.info(f"마찰손실계수 (f₂, 자동계산): **{f2_coeff:.3f}**")
        
    g_val = 9.8 
    pipe_V_sub = math.sqrt((2 * g_val * delta_H) / total_loss_coeff)
    pipe_Q_sub = pipe_A * pipe_V_sub

    with st.container(border=True):
        st.write("**[손실계수 산출]**")
        st.latex(rf"f_2 = \frac{{124.5 \times n^2}}{{D^{{4/3}}}} \times L = \frac{{124.5 \times {pipe_n:.3f}^2}}{{{pipe_D:.2f}^{{4/3}}}} \times {pipe_L:.2f} = {f2_coeff:.3f}")
        st.latex(rf"\Sigma f = f_1 + f_2 + f_3 = {f1_coeff:.2f} + {f2_coeff:.3f} + {f3_coeff:.2f} = {total_loss_coeff:.3f}")
        
        st.divider()
        st.write("**[고조시 최대 유속 및 유량 산출]**")
        st.latex(r"V = \sqrt{\frac{2g \Delta H}{\Sigma f}}")
        st.latex(rf"V = \sqrt{{\frac{{2 \times {g_val} \times {delta_H:.2f}}}{{{total_loss_coeff:.3f}}}}} = {pipe_V_sub:.3f} \, m/s")
        st.latex(rf"Q = A \times V = {pipe_A:.3f} \times {pipe_V_sub:.3f} = {pipe_Q_sub:.3f} \, m^3/s")
        st.success(f"👉 **고조시 최대 유량($Q$)** = {pipe_Q_sub:.3f} m³/s")

    st.markdown("#### 3) 검토결과")
    design_pipe_Q = min(pipe_Q_man, pipe_Q_sub)
    with st.container(border=True):
        st.write("자연유하시 유량과 고조시 유량을 비교하여 더 불리한(작은) 값을 해당 관경의 설계 1련 통수능력으로 결정합니다.")
        
        df_compare_pipes = pd.DataFrame({
            "구분": ["1) 자연유하 최대 유량", "2) 고조시 최대 유량"],
            "산정 공식": ["Manning 공식", "손실수두 고려식"],
            "산정 유량 (m³/s)": [f"{pipe_Q_man:.3f}", f"{pipe_Q_sub:.3f}"]
        })
        st.table(df_compare_pipes.set_index("구분"))
        st.info(f"💡 **적용 1련 통수능력($q$)** = min({pipe_Q_man:.3f}, {pipe_Q_sub:.3f}) = **{design_pipe_Q:.3f} m³/s**")

    # --- 나. 여수토관 소요개수 ---
    st.markdown("### 나. 여수토관 소요개수")
    calc_pipe_count = design_q_final / design_pipe_Q
    default_pipe_count = math.ceil(calc_pipe_count)
    
    with st.container(border=True):
        st.write(f"앞서 산정한 최종 계획유출량($Q_{{design}}$ = {design_q_final:.3f} m³/s)을 안정적으로 배제하기 위한 소요 개수 산정")
        st.latex(r"N = \frac{Q_{design}}{q}")
        st.write(f"👉 계산된 필요 개수 = {design_q_final:.3f} / {design_pipe_Q:.3f} = **{calc_pipe_count:.2f} 개**")
        
        st.divider()
        st.markdown("#### ⚙️ 최종 적용 여수토관 개수 결정")
        final_pipe_count = st.number_input("설계에 반영할 최종 여수토관 개수 (EA)", min_value=1, value=default_pipe_count, step=1)
        
        st.success(f"💡 **최종 적용 여수토관 개수 = {final_pipe_count} 개 (D={pipe_D}m)**")

    # --- 다. 집수정 월류폭 산정 (Bazin 공식 적용) ---
    st.markdown("### 다. 집수정 월류폭 산정 (Bazin 공식 적용)")
    c3_7, c3_8 = st.columns(2)
    with c3_7:
        weir_H = st.number_input("허용 월류수심 (h, m)", value=1.00, format="%.2f")
    with c3_8:
        basin_count = st.number_input("집수정 설치개수 (EA)", value=12, step=1)
        
    q_per_basin = design_q_final / basin_count if basin_count > 0 else design_q_final
    
    bazin_C = 0.405 + (0.003 / weir_H) if weir_H > 0 else 0
    req_weir_B = q_per_basin / (bazin_C * math.sqrt(2 * g_val) * (weir_H**1.5)) if bazin_C > 0 else 0
    
    with st.container(border=True):
        st.markdown("#### 📖 Bazin 월류폭 산정 공식")
        st.write("Bazin의 사각 위어(Weir) 공식을 적용하여 집수정 1개소당 필요한 월류폭(B)을 산정합니다.")
        st.latex(r"Q = \left( 0.405 + \frac{0.003}{h} \right) \times B \times \sqrt{2g} \times h^{3/2}")
        st.markdown("""
        * $Q$ : 집수정 1개소당 처리할 유출량 ($m^3/sec$)
        * $B$ : 소요 월류폭 ($m$)
        * $h$ : 허용 월류고 ($m$)
        * $g$ : 중력가속도 ($9.8 \, m/sec^2$)
        """)
        
        st.divider()
        st.markdown("#### 📝 상세 풀이 과정")
        st.write(f"- 적용 총 유출량 ($Q_{{total}}$) = {design_q_final:.3f} $m^3/sec$")
        st.write(f"- 집수정 1개소당 유출량 ($Q$) = {design_q_final:.3f} / {basin_count} = **{q_per_basin:.4f} $m^3/sec$**")
        st.write(f"- 적용 월류고 ($h$) = {weir_H:.2f} $m$")
        
        st.latex(rf"{q_per_basin:.4f} = \left( 0.405 + \frac{{0.003}}{{{weir_H:.2f}}} \right) \times B \times \sqrt{{2 \times 9.8}} \times {weir_H:.2f}^{{1.5}}")
        st.latex(rf"{q_per_basin:.4f} = {bazin_C:.4f} \times B \times {math.sqrt(2*g_val):.4f} \times {weir_H**1.5:.4f}")
        st.latex(rf"B = \frac{{{q_per_basin:.4f}}}{{{bazin_C * math.sqrt(2*g_val) * (weir_H**1.5):.4f}}} = {req_weir_B:.3f} \, m")
        
        st.info(f"👉 **집수정당 소요 월류폭 계산값** = {req_weir_B:.3f} m 이상")
        
        st.divider()
        st.markdown("#### ⚙️ 월류폭 최종 결정")
        final_weir_B = st.number_input("설계에 반영할 최종 집수정당 월류폭 (m)", min_value=math.ceil(req_weir_B * 10) / 10.0 if req_weir_B > 0 else 1.0, value=max(2.0, math.ceil(req_weir_B * 10) / 10.0), step=0.1)
        st.success(f"🏆 **최종 결정 월류폭($B$)** = {final_weir_B:.2f} m")

    # --- 마. 여수토 전면 피복석 산정 (Isbash 공식) ---
    st.markdown("### 마. 여수토 전면 피복석 산정 (Isbash 공식)")
    c3_9, c3_10 = st.columns(2)
    with c3_9:
        stone_gamma = st.number_input("피복석 단위중량 (γ_r, kN/m³)", value=26.0, format="%.1f")
        water_gamma = st.number_input("해수 단위중량 (γ_w, kN/m³)", value=10.1, format="%.1f")
    with c3_10:
        y_options = {"피묻힌 돌 (1.20)": 1.20, "노출된 돌 (0.86)": 0.86}
        selected_y_desc = st.selectbox("Isbash 계수 (y) 선택", list(y_options.keys()), index=0)
        isbash_y = y_options[selected_y_desc]
        
        target_V = st.number_input("설계 유속 (V, m/s)", value=4.91, format="%.2f")

    # 비중, 직경, 체적, 중량 계산
    sr = stone_gamma / water_gamma
    stone_d = (target_V**2) / (2 * g_val * (isbash_y**2) * (sr - 1))
    
    stone_Vol = (math.pi / 6) * (stone_d**3) # m^3 (루베)
    stone_W_kN = stone_Vol * stone_gamma     # kN
    stone_W_ton = stone_W_kN / g_val         # ton (1 tonf = 9.8 kN 환산)
    stone_W_kg = stone_W_ton * 1000          # kg

    with st.container(border=True):
        st.markdown("#### 📖 피복석 소요중량 산정 공식")
        st.write("배출 유속에 견디기 위한 전면 사석 피복석의 직경 및 중량을 산정합니다.")
        st.latex(r"d = \frac{V^2}{2g \cdot y^2 \left(\frac{\gamma_r}{\gamma_w} - 1\right)}, \quad W = \frac{\pi d^3}{6} \gamma_r")
        st.markdown("""
        * $V$ : 설계 유속 ($m/s$)
        * $g$ : 중력가속도 ($9.8 \, m/s^2$)
        * $y$ : Isbash 계수 (피묻힌 돌 1.20 / 노출된 돌 0.86)
        * $\gamma_r$, $\gamma_w$ : 피복석 및 해수 단위중량 ($kN/m^3$)
        """)
        
        st.divider()
        st.markdown("#### 📝 상세 풀이 과정")
        st.write(f"- 비중 ($S_r = \gamma_r / \gamma_w$) = {stone_gamma:.1f} / {water_gamma:.1f} = **{sr:.3f}**")
        st.write(f"- 적용 유속 ($V$) = **{target_V:.2f} m/s**")
        st.write(f"- 적용 Isbash 계수 ($y$) = **{isbash_y:.2f}** ({selected_y_desc})")
        
        st.latex(rf"d = \frac{{{target_V:.2f}^2}}{{2 \times 9.8 \times {isbash_y:.2f}^2 \times ({sr:.3f} - 1)}} = \frac{{{target_V**2:.4f}}}{{{2 * 9.8 * isbash_y**2 * (sr - 1):.4f}}} = {stone_d:.3f} \, m")
        
        st.latex(rf"Vol = \frac{{\pi \times {stone_d:.3f}^3}}{{6}} = {stone_Vol:.4f} \, m^3/\text{{EA}}")
        st.latex(rf"W = {stone_Vol:.4f} \times {stone_gamma:.1f} = {stone_W_kN:.2f} \, kN/\text{{EA}}")
        st.latex(rf"W_{{ton}} \text{{ (환산)}} = \frac{{{stone_W_kN:.2f}}}{{{g_val}}} = {stone_W_ton:.3f} \, \text{{ton/EA}}")
        
        st.success(f"👉 **소요 직경($d$)** = {stone_d:.3f} m  \n👉 **소요 체적($Vol$)** = {stone_Vol:.3f} $m^3$/EA (루베)  \n👉 **소요 중량($W$)** = {stone_W_kN:.2f} kN/EA (환산: {stone_W_kg:.1f} kg/EA, {stone_W_ton:.3f} ton/EA)")


    # ==========================================
    # ★ 바. 여수토 본체설계 ★
    # ==========================================
    st.divider()
    st.header("바. 여수토 본체설계")
    
    st.markdown("### 1) 여수토 단면 및 2) 설계조건")
    with st.expander("📝 설계조건 상세 (가~자) 보기 및 입력", expanded=True):
        tab1, tab2, tab3 = st.tabs(["가~다 (설계법/조위/표고)", "라~바 (투기고/규격/하중)", "사~자 (마찰각/토류판/기타)"])
        
        with tab1:
            st.write("**가) 구조물 설계법:** 극한강도 설계법 이용")
            st.write("**나) 조위 (m):**")
            c_wl1, c_wl2, c_wl3 = st.columns(3)
            wl_ahhw = c_wl1.number_input("A.H.H.W", value=7.246, format="%.3f")
            wl_msl = c_wl2.number_input("평균해면", value=3.623, format="%.3f")
            wl_datum = c_wl3.number_input("기본수준면", value=0.000, format="%.3f")
            
            st.write("**다) 천단고 및 저면고 (El.m):**")
            c_el1, c_el2 = st.columns(2)
            el_top = c_el1.number_input("여수토 천단고", value=12.80, format="%.2f")
            el_bot = c_el2.number_input("여수토 저면고", value=4.55, format="%.2f")
            
        with tab2:
            st.write("**라) 준설토 투기고 (El.m):**")
            el_fill = st.number_input("투기고", value=12.30, format="%.2f")
            
            st.write("**마) 여수토 규격 및 벽체 두께:**")
            c_dim1, c_dim2, c_dim3 = st.columns(3)
            spillway_W = c_dim1.number_input("여수토 폭 (m)", value=2.70, format="%.2f")
            spillway_L = c_dim2.number_input("여수토 길이 (m)", value=13.10, format="%.2f")
            spillway_H = c_dim3.number_input("여수토 높이 (m)", value=8.25, format="%.2f")
            
            c_thk1, c_thk2, c_thk3, c_thk4, c_thk5 = st.columns(5)
            thk_front = c_thk1.number_input("전면벽 두께 (cm)", value=25, step=1)
            thk_side = c_thk2.number_input("측면벽 두께 (cm)", value=25, step=1)
            thk_part = c_thk3.number_input("격벽 두께 (cm)", value=25, step=1)
            thk_rear = c_thk4.number_input("후면벽 두께 (cm)", value=40, step=1)
            thk_bot = c_thk5.number_input("저판 두께 (cm)", value=30, step=1)
            
            st.write("**바) 단위중량 (kN/m³):**")
            c_wt1, c_wt2, c_wt3, c_wt4 = st.columns(4)
            gamma_plain_conc_above = c_wt1.number_input("무근콘크리트(수상)", value=22.6, format="%.1f")
            gamma_plain_conc_sub = c_wt2.number_input("무근콘크리트(수중)", value=12.6, format="%.1f")
            gamma_rebar_conc_above = c_wt3.number_input("철근콘크리트(수상)", value=24.0, format="%.1f")
            gamma_rebar_conc_sub = c_wt4.number_input("철근콘크리트(수중)", value=14.0, format="%.1f")
            
            c_wt5, c_wt6, c_wt7, c_wt8 = st.columns(4)
            gamma_wood_above = c_wt5.number_input("목재(수상)", value=0.8, format="%.1f")
            gamma_sub_soil_b = c_wt6.number_input("준설토(수중)", value=4.9, format="%.1f")
            gamma_water_b = c_wt7.number_input("해수", value=10.1, format="%.1f")
            gamma_backfill_above = c_wt8.number_input("배면토사(수상)", value=18.0, format="%.1f")
            
        with tab3:
            st.write("**사) 마찰각:**")
            st.markdown("**(1) 내부 마찰각**")
            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1:
                phi_dredge_above = st.number_input("준설토 (수상, °)", value=0.0, format="%.1f")
                phi_dredge_sub = st.number_input("준설토 (수중, °)", value=0.0, format="%.1f")
            with col_f2:
                phi_sand_above = st.number_input("모래 (수상, °)", value=30.0, format="%.1f")
                phi_sand_sub = st.number_input("모래 (수중, °)", value=30.0, format="%.1f")
            with col_f3:
                phi_stone_above = st.number_input("사석 (수상, °)", value=40.0, format="%.1f")
                phi_stone_sub = st.number_input("사석 (수중, °)", value=40.0, format="%.1f")
            
            st.markdown("**(2) 벽면 마찰각과 기타**")
            col_w1, col_w2, col_w3 = st.columns(3)
            with col_w1:
                delta_angle = st.number_input("벽면 마찰각 (δ, °)", value=15.0, format="%.1f")
            with col_w2:
                beta_angle = st.number_input("지표면과 수평이루는각 (β, °)", value=0.0, format="%.1f")
            with col_w3:
                phi_wall_angle = st.number_input("벽면이 연직과이루는각 (φ, °)", value=0.0, format="%.1f")
            
            st.write("**아) Wood square timber (문비/토류판):**")
            c_wd1, c_wd2, c_wd3, c_wd4 = st.columns(4)
            wood_h = c_wd1.number_input("토류판 높이 (m)", value=0.20, format="%.2f")
            wood_t = c_wd2.number_input("토류판 폭 (m)", value=0.17, format="%.2f")  
            wood_l = c_wd3.number_input("토류판 지간 (m)", value=2.00, format="%.2f")
            wood_allow = c_wd4.number_input("SS275 허용응력 (MPa)", value=183.0, format="%.1f")
            
            st.write("**자) 여수토 본체:** 슬래브의 계산수표(항만 및 어항 설계기준) 적용")
            
    st.markdown("### 3) 단면검토 (Wood square timber)")
    with st.container(border=True):
        st.write("준설토와 해수를 구분하여 해수만 여수토 내로 유출될 수 있도록 하는 역할을 하며, 작용하는 토압에 충분히 견딜 수 있도록 계획합니다.")
        st.info("💡 **단면검토 핵심:** 가장 하단에 설치되는 토류판이 가장 큰 토압을 받으므로 이를 기준으로 극한응력을 산정하여 안정성을 확인합니다.")
        
        st.markdown("#### ① 작용 토압 (Coulomb 공식) 및 수압 산정")
        c_p1, c_p2 = st.columns(2)
        with c_p1:
            h_soil = st.number_input("준설토압 작용수심 (m)", value=6.95, format="%.2f")
        with c_p2:
            h_water = st.number_input("수압 작용수심 (m)", value=7.45, format="%.2f")
            
        ko = 1.0 # 정지토압계수 (점성토 적용시)
        p_soil = ko * h_soil * gamma_sub_soil_b
        p_water = ko * h_water * gamma_water_b
        p_total = p_soil + p_water
        
        st.latex(rf"P_{{soil}} = K_o \times H_{{soil}} \times \gamma_{{sub}} = {ko} \times {h_soil} \times {gamma_sub_soil_b} = {p_soil:.3f} \, kN/m^2")
        st.latex(rf"P_{{water}} = K_o \times H_{{water}} \times \gamma_w = {ko} \times {h_water} \times {gamma_water_b} = {p_water:.3f} \, kN/m^2")
        st.latex(rf"P_{{total}} = P_{{soil}} + P_{{water}} = \mathbf{{{p_total:.3f} \, kN/m^2}}")
        
        st.divider()
        st.markdown("#### ② 하중 및 최대 휨모멘트 산정")
        w_max = p_total * wood_h
        m_max = w_max * (wood_l**2) / 8
        
        st.latex(rf"W_{{max}} = P_{{total}} \times \text{{토류판 높이}} = {p_total:.3f} \times {wood_h} = {w_max:.3f} \, kN/m")
        st.latex(rf"M_{{max}} = \frac{{W_{{max}} \times l^2}}{{8}} = \frac{{{w_max:.3f} \times {wood_l}^2}}{{8}} = \mathbf{{{m_max:.3f} \, kN\cdot m}}")
        
        st.divider()
        st.markdown("#### ③ 강재(SS275) 허용응력 검토")
        # mm 단위 변환 계산
        b_mm = wood_h * 1000
        t_mm = wood_t * 1000
        Z_mm3 = (b_mm * (t_mm**2)) / 6
        
        stress_mpa = (m_max * 1e6) / Z_mm3
        
        st.latex(rf"Z \, \text{{(단면계수)}} = \frac{{b \times t^2}}{{6}} = \frac{{{b_mm:.0f} \times {t_mm:.0f}^2}}{{6}} = {Z_mm3:.1f} \, mm^3")
        st.latex(rf"f \, \text{{(발생응력)}} = \frac{{M_{{max}}}}{{Z}} = \frac{{{m_max:.3f} \times 10^6}}{{{Z_mm3:.1f}}} = {stress_mpa:.3f} \, MPa")
        
        if stress_mpa < wood_allow:
            st.success(f"🏆 **결과: 발생응력 {stress_mpa:.3f} MPa < 허용응력 {wood_allow} MPa ∴ 안전(O.K)**")
        else:
            st.error(f"⚠️ **결과: 발생응력 {stress_mpa:.3f} MPa >= 허용응력 {wood_allow} MPa ∴ 불안정(N.G)**")

    # ==========================================
    # ★ 5. 최종 계산 보고서 다운로드 기능 (수식 오류 완벽 해결) ★
    # ==========================================
    st.divider()
    st.header("🖨️ 5. 상세 계산 보고서 다운로드")
    st.write("위에서 입력한 모든 설계조건과 상세 수식 풀이과정을 포함한 **보고서(HTML)**를 다운로드할 수 있습니다.")
    st.info("💡 **수식 안 깨지게 활용하는 법:** 다운로드한 HTML 파일을 **웹 브라우저(크롬, 엣지 등)**로 엽니다. 브라우저에서 깔끔하게 렌더링된 수식을 확인한 뒤, 브라우저의 **'인쇄(Ctrl+P)' 기능으로 PDF로 저장**하거나 **전체 복사하여 한글(HWP)/워드에 붙여넣기** 하시면 수식이 완벽하게 복사됩니다.")

    report_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>여수토 설계 상세 계산 보고서</title>
<script>
  MathJax = {{
    tex: {{
      inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
      displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
      processEscapes: true
    }}
  }};
</script>
<script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<style>
    body {{ font-family: 'Malgun Gothic', sans-serif; line-height: 1.6; margin: 40px; color: #333; }}
    h1, h2, h3 {{ color: #2c3e50; border-bottom: 1px solid #eee; padding-bottom: 5px; margin-top: 30px; }}
    .box {{ border: 1px solid #ddd; padding: 15px; margin-bottom: 20px; background-color: #f9f9f9; border-radius: 5px; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; font-size: 0.95em; }}
    th, td {{ border: 1px solid #ccc; padding: 10px; text-align: left; }}
    th {{ background-color: #f0f0f0; }}
</style>
</head>
<body>
<h1>🌊 여수토 단면 및 본체 설계 상세 보고서</h1>

<h2>1. 설계 입력 조건 요약</h2>
<table>
    <tr><th>구분</th><th>입력 항목</th><th>적용 값</th></tr>
    <tr><td rowspan="3">펌프준설선</td><td>배송거리 / 토사경도(N)</td><td>{input_dist} km / {input_n}</td></tr>
    <tr><td>정격출력 / 환산계수(f)</td><td>{p_kw_auto:,.2f} kW / {f_factor}</td></tr>
    <tr><td>종합 작업효율(E)</td><td>{eff_total:.2f}</td></tr>
    <tr><td rowspan="3">유출량 산정</td><td>준설선 대수 / 함니율</td><td>{d_count} 대 / {m_ratio}</td></tr>
    <tr><td>유역면적 / 유출계수 / 유입시간</td><td>{area_ha} ha / {run_c} / {t_min_calc} 분</td></tr>
    <tr><td>적용 강우강도</td><td>{max_rain_i:.2f} mm/hr</td></tr>
    <tr><td rowspan="3">여수토관 및 월류폭</td><td>여수토관 관경(D) / 조도계수(n) / 경사(S)</td><td>{pipe_D} m / {pipe_n} / {pipe_S}</td></tr>
    <tr><td>허용수위차($\\Delta H$) / 관연장(L) / 총손실계수($\\Sigma f$)</td><td>{delta_H} m / {pipe_L} m / {total_loss_coeff:.3f}</td></tr>
    <tr><td>집수정 허용 월류고(h) / 설치개수</td><td>{weir_H} m / {basin_count} EA</td></tr>
    <tr><td rowspan="2">피복석</td><td>피복석($\\gamma_r$) / 해수($\\gamma_w$) 단위중량</td><td>{stone_gamma} kN/m³ / {water_gamma} kN/m³</td></tr>
    <tr><td>Isbash 계수(y) / 설계유속(V)</td><td>{isbash_y} / {target_V} m/s</td></tr>
    <tr><td rowspan="2">본체설계 (토류판)</td><td>토류판 높이 / 폭 / 지간</td><td>{wood_h} m / {wood_t} m / {wood_l} m</td></tr>
    <tr><td>준설토 / 해수 단위중량, 적용수심</td><td>{gamma_sub_soil_b} kN/m³, {h_soil}m / {gamma_water_b} kN/m³, {h_water}m</td></tr>
</table>

<h2>2. 상세 풀이 과정 및 결과</h2>

<h3>가. 펌프준설선 시간당 준설량 ($Q_s$)</h3>
<div class="box">
    <p>$$b_0 = P \\times f = {p_kw_auto:,.2f} \\times f_factor = {b0_kw_res:,.2f} \\, kW$$</p>
    <p>$$Q_s = q \\times \\frac{{{b0_kw_res:,.2f}}}{{746}} \\times E = {q_read:,.2f} \\times \\frac{{{b0_kw_res:,.2f}}}{{746}} \\times {eff_total:.2f} = \\mathbf{{{qs_res:,.2f} \\, m^3/hr}}$$</p>
</div>

<h3>나. 최종 계획 유출량 ($Q_{{design}}$)</h3>
<div class="box">
    <p>1) 준설선 유입량 ($Q_1$):</p>
    <p>$$W = \\frac{{1 - {m_ratio}}}{{{m_ratio}}} = {calc_water_ratio:.2f}$$</p>
    <p>$$Q_1 = \\frac{{{qs_res:,.2f} \\times {d_count} \\times (1 + {calc_water_ratio:.2f})}}{{3600}} = {calc_dredge_q:,.3f} \\, m^3/sec$$</p>
    <p>2) 강우 유출량 ($Q_2$): 최대 강우강도 {max_rain_i:.2f} mm/hr 적용</p>
    <p>$$Q_2 = \\frac{{1}}{{3.6}} \\times {run_c} \\times {max_rain_i:.2f} \\times {area_ha/100:.4f} = {rain_q_max:,.3f} \\, m^3/sec$$</p>
    <p>3) 최종 유출량 결정:</p>
    <p>$$Q_{{design}} = \\max(Q_1, Q_2) = \\mathbf{{{design_q_final:,.3f} \\, m^3/sec}}$$</p>
</div>

<h3>다. 여수토관 통수능력 및 소요개수</h3>
<div class="box">
    <p>1) 자연유하시 최대 유량 (Manning): $Q_{{man}} = {pipe_Q_man:.3f} \\, m^3/s$</p>
    <p>2) 고조시 배출가능 유량: $Q_{{sub}} = {pipe_Q_sub:.3f} \\, m^3/s$</p>
    <p>3) 1련 통수능력 ($q$) 적용: $\\min({pipe_Q_man:.3f}, {pipe_Q_sub:.3f}) = {design_pipe_Q:.3f} \\, m^3/s$</p>
    <p>4) 소요개수 ($N$):</p>
    <p>$$N = \\frac{{Q_{{design}}}}{{q}} = \\frac{{{design_q_final:.3f}}}{{{design_pipe_Q:.3f}}} = {calc_pipe_count:.2f} \\rightarrow \\text{{최종 적용: }} \\mathbf{{{final_pipe_count} \\text{{ 개}}}}$$</p>
</div>

<h3>라. 집수정 월류폭 산정 (Bazin 공식)</h3>
<div class="box">
    <p>집수정 1개소당 할당 유량 $Q = {q_per_basin:.4f} \\, m^3/s$</p>
    <p>$$B = \\frac{{Q}}{{\\left(0.405 + \\frac{{0.003}}{{h}}\\right) \\times \\sqrt{{2g}} \\times h^{{3/2}}}} = \\frac{{{q_per_basin:.4f}}}{{\\left(0.405 + \\frac{{0.003}}{{{weir_H:.2f}}}\\right) \\times \\sqrt{{2 \\times 9.8}} \\times {weir_H:.2f}^{{1.5}}}} = {req_weir_B:.3f} \\, m$$</p>
    <p>최종 결정 월류폭 ($B$) = $\\mathbf{{{final_weir_B:.2f} \\, m}}$</p>
</div>

<h3>마. 피복석 소요중량 산정 (Isbash 공식)</h3>
<div class="box">
    <p>비중 $S_r = {sr:.3f}$, 설계유속 $V = {target_V:.2f} \\, m/s$, Isbash 계수 $y = {isbash_y:.2f}$</p>
    <p>$$d = \\frac{{{target_V:.2f}^2}}{{2 \\times 9.8 \\times {isbash_y:.2f}^2 \\times ({sr:.3f} - 1)}} = {stone_d:.3f} \\, m$$</p>
    <p>$$W = \\frac{{\\pi \\times {stone_d:.3f}^3}}{{6}} \\times {stone_gamma:.1f} = \\mathbf{{{stone_W_kN:.2f} \\, kN/EA}} \\text{{ (환산: {stone_W_ton:.3f} ton/EA)}}$$</p>
</div>

<h3>바. 여수토 본체설계 단면검토 (토류판 응력검토)</h3>
<div class="box">
    <p>1) 작용 토압 및 수압:</p>
    <p>$$P_{{total}} = (1.0 \\times {h_soil} \\times {gamma_sub_soil_b}) + (1.0 \\times {h_water} \\times {gamma_water_b}) = \\mathbf{{{p_total:.3f} \\, kN/m^2}}$$</p>
    <p>2) 등분포하중 및 휨모멘트:</p>
    <p>$$W_{{max}} = {p_total:.3f} \\times {wood_h} = {w_max:.3f} \\, kN/m$$</p>
    <p>$$M_{{max}} = \\frac{{{w_max:.3f} \\times {wood_l}^2}}{{8}} = {m_max:.3f} \\, kN\\cdot m$$</p>
    <p>3) 단면계수 및 발생응력 검토:</p>
    <p>$$Z = \\frac{{{b_mm:.0f} \\times {t_mm:.0f}^2}}{{6}} = {Z_mm3:.1f} \\, mm^3$$</p>
    <p>$$f = \\frac{{{m_max:.3f} \\times 10^6}}{{{Z_mm3:.1f}}} = \\mathbf{{{stress_mpa:.3f} \\, MPa}}$$</p>
    <p>결과: 발생응력 ({stress_mpa:.3f} MPa) &lt; 허용응력 ({wood_allow} MPa) ∴ <strong>{"안전(O.K)" if stress_mpa < wood_allow else "불안정(N.G)"}</strong></p>
</div>

</body>
</html>
"""
    
    st.download_button(
        label="📄 상세 계산 보고서 다운로드 (HTML)",
        data=report_html.encode('utf-8'),
        file_name="여수토_설계_상세계산서.html",
        mime="text/html"
    )

if __name__ == "__main__":
    main()
