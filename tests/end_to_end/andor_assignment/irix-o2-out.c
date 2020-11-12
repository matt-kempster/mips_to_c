s32 test(s32 arg0, s32 arg1, s32 arg2, s32 arg3) {
    s32 sp24;
    s32 sp20;
    s32 temp_s0;
    s32 temp_s0_2;
    s32 temp_t3;
    s32 temp_t5;
    s32 temp_t7;
    s32 temp_v0;
    s32 temp_v0_2;
    s32 temp_v1;
    s32 temp_v1_2;
    s32 phi_s0;
    s32 phi_t0;
    s32 phi_v1;
    s32 phi_a2;
    s32 phi_v1_2;
    s32 phi_s0_2;
    s32 phi_t0_2;
    s32 phi_v1_3;
    s32 phi_v1_4;
    s32 phi_v1_5;
    s32 phi_v1_6;
    s32 phi_v1_7;

    temp_s0 = arg0 + arg1;
    temp_t7 = arg1 + arg2;
    sp20 = temp_t7;
    phi_s0 = temp_s0;
    phi_t0 = temp_t7;
    if ((temp_s0 != 0) || (phi_s0 = temp_s0, phi_t0 = temp_t7, (temp_t7 != 0)) || (temp_v0 = func_00400090(temp_t7), phi_s0 = temp_s0, phi_t0 = temp_v0, (temp_v0 != 0)) || (phi_s0 = 2, phi_t0 = temp_v0, (arg3 != 0))) {
        phi_v1 = 1;
    } else {
        phi_s0 = 2;
        phi_t0 = temp_v0;
        phi_v1 = -2;
        phi_a2 = arg2;
        if (arg0 != 0) {
            phi_s0 = 2;
            phi_t0 = temp_v0;
            phi_v1 = -1;
            phi_a2 = arg2;
        }
    }
    temp_v1 = phi_v1 + phi_a2;
    phi_s0_2 = phi_s0;
    phi_t0_2 = phi_t0;
    phi_v1_3 = temp_v1;
    if (phi_s0 != 0) {
        phi_s0_2 = phi_s0;
        phi_t0_2 = phi_t0;
        phi_v1_3 = temp_v1;
        if (phi_t0 != 0) {
            temp_s0_2 = phi_s0 + phi_t0;
            sp24 = temp_v1;
            temp_v0_2 = func_00400090(temp_s0_2);
            phi_s0_2 = temp_s0_2;
            phi_t0_2 = temp_v0_2;
            phi_v1_3 = temp_v1;
            if (temp_v0_2 != 0) {
                phi_s0_2 = temp_s0_2;
                phi_t0_2 = temp_v0_2;
                phi_v1_3 = temp_v1;
                if (arg3 != 0) {
                    phi_v1_2 = temp_v1;
                    phi_v1_6 = temp_v1;
                    if (temp_v1 < 5) {
                        do {
                            temp_t3 = (phi_v1_2 + 1) * 2;
                            phi_v1_2 = temp_t3;
                            phi_v1_6 = temp_t3;
                        } while ((temp_t3 < 5) != 0);
                    }
                    phi_s0_2 = temp_s0_2;
                    phi_t0_2 = temp_v0_2;
                    phi_v1_3 = phi_v1_6 + 5;
                }
            }
        }
    }
    if ((phi_s0_2 != 0) && (phi_t0_2 != 0) && (sp24 = phi_v1_3, temp_v1_2 = phi_v1_3, (func_00400090(phi_s0_2 + phi_t0_2) != 0)) && (arg3 != 0)) {
        phi_v1_4 = temp_v1_2;
        phi_v1_7 = temp_v1_2;
        if (temp_v1_2 < 5) {
            do {
                temp_t5 = (phi_v1_4 + 1) * 2;
                phi_v1_4 = temp_t5;
                phi_v1_7 = temp_t5;
            } while ((temp_t5 < 5) != 0);
        }
        phi_v1_5 = phi_v1_7 + 5;
    } else {
        phi_v1_5 = phi_v1_3 + 6;
    }
    return phi_v1_5;
}
