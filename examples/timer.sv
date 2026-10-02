// A flat module, as most first designs are: no instances, only processes.

module timer #(
    parameter int W = 4
) (
    input  logic         clk,
    input  logic         rst_n,
    input  logic         start,
    output logic [W-1:0] value,
    output logic         done
);
  logic         running;
  logic [W-1:0] next;

  always_comb begin
    next = running ? value + 1'b1 : value;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      value   <= '0;
      running <= 1'b0;
    end else begin
      value <= next;
      if (start) running <= 1'b1;
      else if (done) running <= 1'b0;
    end
  end

  assign done = value == '1;
endmodule
