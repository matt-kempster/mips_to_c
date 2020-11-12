s32 test(s32 arg0) {
    s32 phi_v1;
    s32 phi_v1_2;
    s32 phi_v1_3;

    phi_v1_3 = 0;
    if (arg0 > 0) {
        phi_v1 = 0;
        do {
            if (phi_v1 == 5) {
                phi_v1_2 = phi_v1 * 2;
            } else {
                phi_v1_2 = phi_v1 + 4;
            }
            phi_v1 = phi_v1_2;
            phi_v1_3 = phi_v1_2;
        } while ((phi_v1_2 < arg0) != 0);
    }
    return phi_v1_3;
}
