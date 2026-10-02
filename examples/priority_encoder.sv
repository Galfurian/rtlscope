// A priority encoder written with a loop in a combinational process.

module priority_encoder (
    input  logic [7:0] req,
    output logic [2:0] grant,
    output logic       valid
);
  always_comb begin
    grant = '0;
    for (int i = 0; i < 8; i++) begin
      if (req[i]) grant = 3'(i);
    end
  end

  assign valid = req != 8'd0;
endmodule
