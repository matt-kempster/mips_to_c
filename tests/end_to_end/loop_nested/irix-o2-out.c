s32 test(s32 arg0) {
    s32 temp_v0;
    s32 temp_v1;
    s32 phi_a3;
    s32 phi_v0;
    s32 phi_v1;
    s32 phi_v1_2;
    s32 phi_v1_3;
    s32 phi_a2;
    s32 phi_v1_4;

    phi_v0 = 0;
    phi_v1 = 0;
    phi_v1_4 = 0;
    if (arg0 > 0) {
        do {
            phi_v1_2 = phi_v1_4;
            if (arg0 > 0) {
                phi_a3 = 1;
                phi_v1_3 = phi_v1_4;
                phi_a2 = phi_v0 * 0;
                do {
                    temp_v1 = phi_v1_3 + phi_a2;
                    phi_a3 = phi_a3 + 1;
                    phi_v1_2 = temp_v1;
                    phi_v1_3 = temp_v1;
                    phi_a2 = phi_a2 + phi_v0;
                } while (arg0 != phi_a3);
            }
            temp_v0 = phi_v0 + 1;
            phi_v0 = temp_v0;
            phi_v1 = phi_v1_2;
            phi_v1_4 = phi_v1_2;
        } while (temp_v0 != arg0);
    }
    return phi_v1;
}
